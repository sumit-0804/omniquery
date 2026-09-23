from functools import lru_cache

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from Models.schema import ETLAgentSchema
from utils.etl_tools import ETLTools
from utils.llm_pick import pick_llm, text_of
from utils.paths import resolve_under_root
from utils.result import Result
from utils.sandbox import run_generated_code

MAX_TOOL_FAILURES = 2


@tool
def extract_load_tool(url: str, output_folder: str, output_format: str = "csv") -> str:
    """
    Extract data from an API endpoint and save it to a folder.

    Args:
        url (str): The API endpoint to extract data from.
        output_folder (str): Folder for the extracted data, relative to the project root
            (for example data/extract).
        output_format (str): csv or json. Defaults to csv.

    Returns:
        str: A message starting with OK: on success or ERROR[code]: on failure.
    """
    return ETLTools().extract_load(url, output_folder, output_format).as_tool_message()


@tool
def transform_load_tool(
    input_file_path: str, output_folder: str, output_format: str, user_question: str
) -> str:
    """
    Transform data from a file with pandas and save the result to a folder.

    Args:
        input_file_path (str): Path to the input file, relative to the project root.
        output_folder (str): Folder for the transformed data, relative to the project root.
        output_format (str): csv, json or parquet.
        user_question (str): What the transformation should do.

    Returns:
        str: A message starting with OK: on success or ERROR[code]: on failure.
    """
    return _transform_load(
        input_file_path, output_folder, output_format, user_question
    ).as_tool_message()


def _transform_load(
    input_file_path: str, output_folder: str, output_format: str, user_question: str
) -> Result[str]:

    source = resolve_under_root(input_file_path)
    if not source:
        return source

    destination = resolve_under_root(output_folder)
    if not destination:
        return destination

    context = ETLTools().describe_file(source.value)
    if not context:
        return context

    destination.value.mkdir(parents=True, exist_ok=True)
    target = destination.value / f"transformed_data.{output_format}"

    llm = pick_llm("high")

    prompt = f"""
    You are a Python data analyst. Write pandas code that performs the transformation
    the user asked for. Output only code, no explanation and no markdown fences.

    Read the data from: {source.value}
    Write the result to: {target}
    Use these paths exactly as given.

    The code runs in an isolated process with no environment variables and no network
    access. Do not import os, subprocess or socket. Use only pandas and plain Python.
    Finish by printing a short summary of the result, for example its shape and head.

    User's question: {user_question}

    Data description:
    {context.value}
    """

    code = _strip_code_fences(text_of(llm.invoke(prompt)))

    outcome = run_generated_code(code, destination.value)
    if not outcome:
        return outcome

    # Exit code 0 means the script ran, not that it wrote what was asked.
    if not target.exists():
        return Result.fail(
            "etl.no_output",
            f"The code ran but did not create {target}. Output was: {outcome.value}",
        )

    return Result.ok(f"Saved {target}. {outcome.value}", path=str(target))


def _strip_code_fences(code: str) -> str:
    code = (code or "").strip()
    if not code.startswith("```"):
        return code

    lines = code.splitlines()
    lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]

    if lines and lines[0].strip().lower() in {"python", "py"}:
        lines = lines[1:]

    return "\n".join(lines).strip()


tools = [extract_load_tool, transform_load_tool]
tools_by_name = {t.name: t for t in tools}

@lru_cache(maxsize=1)
def _tool_llm():
    # Built lazily: provider discovery makes a network call, which must not
    # happen just because someone imported this module.
    return pick_llm("high", tools=tools)


def llm_node(state: ETLAgentSchema) -> dict:

    prompt = f"""
    You are a Python data analyst with tools that extract, transform and load data.
    Perform the ETL operation the user asked for. Tool results start with OK: on success
    or ERROR[code]: on failure. If a tool fails, explain the failure to the user rather
    than claiming success. Once the work is done, tell the user and stop.

    Chat history: {state.messages}
    """

    return {"messages": [_tool_llm().invoke(prompt)]}


def tool_node(state: ETLAgentSchema) -> dict:

    new_messages = []
    failures = 0

    for tool_call in state.messages[-1].tool_calls:
        observation = tools_by_name[tool_call["name"]].invoke(tool_call["args"])
        failed = observation.startswith("ERROR[")
        failures += int(failed)
        new_messages.append(
            ToolMessage(
                content=observation,
                tool_call_id=tool_call["id"],
                status="error" if failed else "success",
            )
        )

    return {"messages": new_messages, "tool_failures": state.tool_failures + failures}


def give_up(state: ETLAgentSchema) -> dict:

    answer = (
        f"I stopped after {state.tool_failures} failed tool calls. "
        f"The last error was: {text_of(state.messages[-1])}"
    )

    return {"messages": [AIMessage(content=answer)]}


etl_analyst_graph = StateGraph(ETLAgentSchema)

etl_analyst_graph.add_node("llm_node", llm_node)
etl_analyst_graph.add_node("tool_node", tool_node)
etl_analyst_graph.add_node("give_up", give_up)

etl_analyst_graph.add_edge(START, "llm_node")


def is_tool_call(state: ETLAgentSchema) -> str:
    if state.tool_failures >= MAX_TOOL_FAILURES:
        return "give_up"
    return "tool_node" if state.messages[-1].tool_calls else "end"


etl_analyst_graph.add_conditional_edges(
    "llm_node", is_tool_call,
    {"tool_node": "tool_node", "give_up": "give_up", "end": END},
)

etl_analyst_graph.add_edge("tool_node", "llm_node")
etl_analyst_graph.add_edge("give_up", END)

etl_analyst = etl_analyst_graph.compile()


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png

    save_graph_png(etl_analyst, "etl_analyst_graph.png")

    response = etl_analyst.invoke({"messages": [HumanMessage(content="""
        Transform the data in data/extract/extracted_data.csv and save it to
        data/transform in csv format, keeping only bulbasaur.
    """)]})
    print(response["messages"][-1].content)
