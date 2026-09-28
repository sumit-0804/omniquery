from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.registry import AGENT_KEYS, REGISTRY
from Models.schema import DataAgentSchema, build_router_schema
from utils.config import REPO_URL, demo_mode
from utils.jev_router import AMBIGUOUS, decide, route_question
from utils.llm_pick import pick_llm, text_of

# Jev decides alone only at or above this: in the Laya/Jev comparison no wrong pick reached
# 0.80 while most right ones did. Below it, the LLM decides.
JEV_TRUST = 0.80

SYSTEM_PROMPT = f"""
You are an expert data engineer. Read the user's request and decide which agent handles it.
Answer with one of: {", ".join(AGENT_KEYS)}, or "unclear".
""" + "\n".join(f"- {key}: {spec.description}" for key, spec in REGISTRY.items()) + """
- unclear: the request is too vague to act on, points back to something not shown, or could
  reasonably mean more than one of the above. Then also give one short question that would
  settle it, and say in one sentence why it is unclear.
A request to save, export or write data to a file is etl, even when the data comes from the database.
A request to plot, chart, graph or visualize data is chart. Asking for numbers without a visual is sql.
"""

ROUTE_QUESTIONS = route_question({key: spec.description for key, spec in REGISTRY.items()})


def router_node(state: DataAgentSchema) -> dict:

    message = state.messages[-1].content

    try:
        decision = decide(message, ROUTE_QUESTIONS)
    except Exception as error:
        # Jev needs the network and an OpenRouter key; without them the LLM decides.
        print(f"Jev routing failed ({error}), using the LLM router")
        return llm_route(message)

    if decision["confidence"] < JEV_TRUST:
        return llm_route(message)
    if decision["route"] == AMBIGUOUS:
        # Clearly vague: ask the user straight away rather than spend an LLM call.
        return {"route_response": "", "route_confidence": decision["confidence"], "route_source": "jev",
                "clarify_question": "Which of these should handle your request?",
                "clarify_why": "The request is too vague to act on as written. Ask me if you're not sure."}
    return {"route_response": decision["route"], "route_confidence": decision["confidence"],
            "route_source": "jev"}


def llm_route(message: str) -> dict:
    llm_router = pick_llm("medium").with_structured_output(build_router_schema(AGENT_KEYS))
    try:
        response = llm_router.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=message)])
    except Exception as error:
        # Every provider failed; the user can still settle it.
        return {"route_response": "", "route_source": "llm",
                "clarify_question": "Which of these should handle your request?",
                "clarify_why": f"I could not decide automatically ({type(error).__name__})."}

    answer = response.model_dump()
    if answer["answer"] in REGISTRY:
        return {"route_response": answer["answer"], "route_confidence": 1.0, "route_source": "llm"}
    return {"route_response": "", "route_source": "llm",
            "clarify_question": answer["question"] or "Which of these did you mean?",
            "clarify_why": answer["why"] or "The request could mean more than one thing."}


# After this many "explain it" replies the run ends and the user rephrases instead.
MAX_CLARIFY_ROUNDS = 3


def ask_human(state: DataAgentSchema) -> dict:
    """Pause and let the user choose sql or etl, or ask for help deciding.

    Resumed with Command(resume=<reply>): a number or agent key routes; an empty reply
    cancels; anything else is a question, answered in plain words before asking again.
    Each round is its own pass through this node, because a resume re-runs the node from
    the top and would otherwise repeat earlier explanation calls.
    """
    options = [{"key": key, "label": spec.description} for key, spec in REGISTRY.items()]
    reply = str(interrupt({
        "question": state.clarify_question or "Which of these should handle your request?",
        "why": state.clarify_why,
        "explanation": state.clarify_explanation,
        "options": options,
    }) or "").strip()

    choice = _as_choice(reply, options)
    if choice:
        return {"route_response": choice, "route_confidence": 1.0, "route_source": "human",
                "clarify_explanation": ""}
    if not reply or reply.lower() in {"cancel", "stop", "quit"}:
        return _stop("Okay, I left it there. Ask again any time.")
    if state.clarify_rounds + 1 >= MAX_CLARIFY_ROUNDS:
        return _stop("Let's start over. Try asking again with a bit more detail about what you want back.")

    return {"clarify_explanation": explain_choice(state.messages[-1].content, reply),
            "clarify_rounds": state.clarify_rounds + 1}


def _as_choice(reply: str, options: list[dict]) -> str:
    keys = [o["key"] for o in options]
    if reply.isdigit() and 1 <= int(reply) <= len(keys):
        return keys[int(reply) - 1]
    return reply.lower() if reply.lower() in keys else ""


def _stop(text: str) -> dict:
    return {"route_response": "", "clarify_explanation": "", "messages": [AIMessage(content=text)]}


def explain_choice(request: str, user_question: str) -> str:
    options = "\n".join(f"- {key}: {spec.description}" for key, spec in REGISTRY.items())
    prompt = f"""
    A user asked a data assistant: "{request}"
    The assistant can handle it in one of these ways:
    {options}

    The assistant only reads the user's data: no option can change, fix or delete what is
    stored in the database. If the request is to change stored data, say so plainly; the
    closest it can do is etl saving a corrected copy as a new file.

    The user is not sure which to pick and asked: "{user_question}"
    In two or three short, plain sentences, explain what each option would give them for
    this particular request. Use everyday words, no jargon. Do not choose for them.
    """
    try:
        return text_of(pick_llm("low").invoke(prompt)).strip()
    except Exception:
        return ("sql answers your question right here, by looking up the data and showing the result. "
                "etl builds new data from it, or fetches it from a URL, and saves it as a file you can reuse. "
                "chart draws the result as a picture. None of them change the data you already have.")


DEMO_ETL_MESSAGE = (
    "Extracts and transforms are turned off in this demo. "
    f"Run OmniQuery locally to use them: {REPO_URL}"
)


def make_agent_node(key: str):
    def node(state: DataAgentSchema) -> dict:
        # The hosted demo never fetches URLs or runs generated code on the server.
        if key == "etl" and demo_mode():
            return {"messages": [AIMessage(content=DEMO_ETL_MESSAGE)]}
        # Looked up at call time, so tests can swap an agent's graph.
        spec = REGISTRY[key]
        result = spec.graph.invoke(spec.build_input(state.messages[-1].content, state.source_id))
        extra = spec.extract_extra(result) if spec.extract_extra else {}
        return {"messages": [AIMessage(content=spec.extract_answer(result))], **extra}

    return node


def route_edge(state: DataAgentSchema) -> str:
    spec = REGISTRY.get(state.route_response)
    return spec.node_name if spec else "ask_human"


def human_edge(state: DataAgentSchema) -> str:
    spec = REGISTRY.get(state.route_response)
    if spec:
        return spec.node_name
    # An explanation was just written: ask again. Otherwise the user cancelled.
    return "ask_human" if state.clarify_explanation else "end"


data_agent_graph = StateGraph(DataAgentSchema)

data_agent_graph.add_node("router_node", router_node)
data_agent_graph.add_node("ask_human", ask_human)
for key, spec in REGISTRY.items():
    data_agent_graph.add_node(spec.node_name, make_agent_node(key))

data_agent_graph.add_edge(START, "router_node")
agent_nodes = {spec.node_name: spec.node_name for spec in REGISTRY.values()}
data_agent_graph.add_conditional_edges("router_node", route_edge, agent_nodes | {"ask_human": "ask_human"})
data_agent_graph.add_conditional_edges(
    "ask_human", human_edge, agent_nodes | {"ask_human": "ask_human", "end": END},
)
for spec in REGISTRY.values():
    data_agent_graph.add_edge(spec.node_name, END)

# The checkpointer is what lets ask_human pause a run and resume it; each run needs a thread_id.
data_agent = data_agent_graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png
    from utils.sources import default_source

    save_graph_png(data_agent, "data_agent_graph.png")

    response = data_agent.invoke({
        "messages": [HumanMessage(content="how many different payment methods are there?")],
        "source_id": default_source().value["id"],
    }, {"configurable": {"thread_id": "demo"}})
    print(response["messages"][-1].content)
