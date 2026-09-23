from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_typesafe import Choice, Noul
from langgraph.graph import END, START, StateGraph

from agents.registry import AGENT_KEYS, REGISTRY, AgentSpec
from Models.schema import DataAgentSchema, build_router_schema
from utils.jev import pick_classifier
from utils.llm_pick import pick_llm

MIN_CONFIDENCE = 0.5
CONFIDENT = 0.8
MAX_AMBIGUITY = 0.6

SYSTEM_PROMPT = f"""
You are an expert data engineer. Read the user query and decide which agent handles it.
Answer with one of: {", ".join(AGENT_KEYS)}.
""" + "\n".join(f"- {key}: {spec.description}" for key, spec in REGISTRY.items())

ROUTE_QUESTIONS = {
    "route": Choice(
        instructions="Which data agent should handle this request?",
        criteria={key: spec.description for key, spec in REGISTRY.items()},
    ),
    "ambiguous": Noul(
        instructions="Is the request too vague to act on without asking a clarifying question?",
        criteria={
            "true": "Cannot be acted on as written because it points back to unstated context or names no concrete subject",
            "false": "Can be acted on as written, even if some details are left to sensible defaults",
        },
    ),
}


def router_node(state: DataAgentSchema) -> dict:

    message = state.messages[-1].content

    try:
        answers = pick_classifier().invoke({"state": message, "questions": ROUTE_QUESTIONS})
        return {
            "route_response": answers.choices["route"].choice,
            "route_confidence": answers.choices["route"].confidence,
            "is_ambiguous": answers.nouls["ambiguous"].noul,
        }

    except Exception as error:
        # Jev needs network, so fall back to the local model when it is unavailable.
        print(f"Jev routing unavailable ({error}), falling back to local LLM")

        llm_router = pick_llm("high").with_structured_output(build_router_schema(AGENT_KEYS))
        response = llm_router.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=message)]
        )
        # The local fallback gives no confidence score, so assume it is usable.
        return {"route_response": response.model_dump()["answer"], "route_confidence": 1.0}


def make_agent_node(spec: AgentSpec):
    def node(state: DataAgentSchema) -> dict:
        result = spec.graph.invoke(spec.build_input(state.messages[-1].content))
        return {"messages": [AIMessage(content=spec.extract_answer(result))]}

    return node


def clarify_node(state: DataAgentSchema) -> dict:

    options = "\n".join(f"- {key}: {spec.description}" for key, spec in REGISTRY.items())
    answer = (
        "I am not sure which kind of request this is. Could you rephrase it?\n"
        f"I can help with:\n{options}"
    )

    return {"messages": [AIMessage(content=answer)]}


data_agent_graph = StateGraph(DataAgentSchema)

data_agent_graph.add_node("router_node", router_node)
data_agent_graph.add_node("clarify_node", clarify_node)
for spec in REGISTRY.values():
    data_agent_graph.add_node(spec.node_name, make_agent_node(spec))

data_agent_graph.add_edge(START, "router_node")


def route_edge(state: DataAgentSchema) -> str:
    spec = REGISTRY.get(state.route_response)
    if spec is None or state.route_confidence < MIN_CONFIDENCE:
        return "clarify_node"
    # Vague wording only blocks when the route itself is also uncertain; the
    # sub-agents already curate the question before acting on it.
    if state.is_ambiguous > MAX_AMBIGUITY and state.route_confidence < CONFIDENT:
        return "clarify_node"
    return spec.node_name


data_agent_graph.add_conditional_edges(
    "router_node",
    route_edge,
    {spec.node_name: spec.node_name for spec in REGISTRY.values()} | {"clarify_node": "clarify_node"},
)

for spec in REGISTRY.values():
    data_agent_graph.add_edge(spec.node_name, END)
data_agent_graph.add_edge("clarify_node", END)

data_agent = data_agent_graph.compile()


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png

    save_graph_png(data_agent, "data_agent_graph.png")

    response = data_agent.invoke(
        {"messages": [HumanMessage(content="how many different payment methods are there?")]}
    )
    print(response["messages"][-1].content)
