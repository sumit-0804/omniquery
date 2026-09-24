import json
import re
import shutil
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from agents.sql_analyst import TABLE_SELECTION_THRESHOLD, select_tables
from Models.schema import ETLAgentSchema
from utils.database import render_schema
from utils.duck import convert_file, quote_ident, write_frame
from utils.etl_tools import fetch_table
from utils.llm_pick import pick_llm, text_of
from utils.result import Result
from utils.sandbox import run_generated_code
from utils.sources import add_output, open_source, output_path, outputs_dir
from utils.sql_guard import check_readonly

MAX_ETL_ATTEMPTS = 3
# The pandas fallback gets this many repair tries after its first attempt.
MAX_CODE_REPAIRS = 2
FORMATS = ("parquet", "csv", "json")

# Only these can improve on another try; the rest fail the same way every time.
_RETRYABLE = {"db.query_failed", "etl.bad_plan"}

_FAILURE_MESSAGE = {
    "db.unreachable": "I could not reach the data source.",
    "db.query_failed": "The database rejected the transform query.",
    "db.schema_failed": "I could not read the tables of the data source.",
    "source.not_found": "That data source is not saved, so I have nothing to work with.",
    "sql.not_readonly": "The transform query was not read-only, so it was not run.",
    "etl.bad_plan": "I could not work out a plan for this request.",
    "etl.http": "I could not download the data from that URL.",
    "etl.not_json": "That URL did not return JSON, so there was no table to save.",
    "etl.no_results_key": "I could not turn the downloaded JSON into a table.",
    "etl.code_failed": "The generated pandas code kept failing.",
    "etl.empty": "There was nothing to save.",
    "etl.too_large": "The result is too large to save.",
}


def read_context(state: ETLAgentSchema) -> dict:

    db = open_source(state.source_id)
    if not db:
        return {"error_code": db.code, "error_detail": db.error}
    catalog = db.value.schema_catalog()
    if not catalog:
        return {"error_code": catalog.code, "error_detail": catalog.error}

    tables = list(catalog.value["tables"])
    if len(tables) > TABLE_SELECTION_THRESHOLD:
        tables = select_tables(state.user_request, catalog.value)
    context = render_schema(catalog.value, tables, db.meta.get("notes")) if tables else ""

    return {"sql_dialect": db.value.dialect, "schema_context": context}


def plan_etl(state: ETLAgentSchema) -> dict:

    dialect = "DuckDB" if state.sql_dialect == "duckdb" else "Postgres"
    prompt = f"""
    You plan data jobs. Reply with exactly one JSON object and nothing else.

    To fetch data from a URL:
    {{"kind": "extract", "url": "https://...", "name": "short_snake_case_name", "format": "parquet"}}

    To build new data from the tables below with one {dialect} SELECT query:
    {{"kind": "transform", "engine": "sql", "sql": "SELECT ...", "name": "short_snake_case_name", "format": "parquet"}}

    Only if a SELECT query cannot express it, a pandas step on one table:
    {{"kind": "transform", "engine": "python", "table": "table_name", "why": "...", "name": "short_snake_case_name", "format": "parquet"}}

    Rules:
    - The query must return every row the user wants. Do not add a LIMIT unless asked.
    - Use "csv" or "json" as the format only if the user asks for it.
    - Use the name the user gives; otherwise make one up that describes the result.

    User's request: {state.user_request}

    Tables in the current source:
    {state.schema_context or "(none yet)"}

    {_failures_block(state.errors)}
    """

    attempt = state.attempts + 1
    plan = _parse_plan(text_of(pick_llm("medium").invoke(prompt)))
    update = {"attempts": attempt, "error_code": "", "error_detail": ""}
    if not plan:
        return update | {"plan": {}, "error_code": plan.code, "error_detail": plan.error,
                         "errors": [{"attempt": attempt, "code": plan.code, "detail": plan.error, "plan": ""}]}
    return update | {"plan": plan.value}


def _failures_block(errors: list[dict]) -> str:
    if not errors:
        return ""
    lines = ["Your previous attempts failed. Fix the plan."]
    for e in errors:
        lines.append(f"attempt {e['attempt']} -> {e['detail']}" + (f"\n  plan: {e['plan']}" if e["plan"] else ""))
    return "\n".join(lines)


def _parse_plan(reply: str) -> Result[dict]:
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    try:
        plan = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        plan = None
    if not isinstance(plan, dict):
        return Result.fail("etl.bad_plan", "The reply was not a JSON object.")

    kind, engine = plan.get("kind"), plan.get("engine")
    if kind == "extract":
        if not str(plan.get("url", "")).startswith(("http://", "https://")):
            return Result.fail("etl.bad_plan", "An extract needs an http(s) url.")
    elif kind == "transform" and engine == "sql":
        if not plan.get("sql"):
            return Result.fail("etl.bad_plan", "A SQL transform needs a sql query.")
    elif kind == "transform" and engine == "python":
        if not plan.get("table"):
            return Result.fail("etl.bad_plan", "A python transform needs a table.")
    else:
        return Result.fail("etl.bad_plan", 'kind must be "extract" or "transform" (engine "sql" or "python").')

    plan["name"] = str(plan.get("name") or "output")
    plan["format"] = plan.get("format") if plan.get("format") in FORMATS else "parquet"
    return Result.ok(plan)


def run_etl(state: ETLAgentSchema) -> dict:

    plan = state.plan
    target = output_path(plan["name"], plan["format"])
    if plan["kind"] == "extract":
        result = _extract(plan, target)
    elif plan["engine"] == "sql":
        result = _transform_sql(state, plan, target)
    else:
        result = _transform_python(state, plan, target)

    if not result:
        target.unlink(missing_ok=True)
        return {"error_code": result.code, "error_detail": result.error,
                "errors": [{"attempt": state.attempts, "code": result.code, "detail": result.error,
                            "plan": json.dumps(plan)}]}
    return {"output": result.value}


def _extract(plan: dict, target: Path) -> Result[dict]:
    table = fetch_table(plan["url"])
    if not table:
        return table
    rows = write_frame(table.value, target, plan["format"])
    return Result.ok({"path": str(target), "rows": rows, "columns": list(table.value.columns)})


def _transform_sql(state: ETLAgentSchema, plan: dict, target: Path) -> Result[dict]:
    checked = check_readonly(plan["sql"], None, dialect=state.sql_dialect)
    if not checked:
        return checked
    db = open_source(state.source_id)
    if not db:
        return db
    planned = db.value.explain_sql(checked.value)
    if not planned:
        return planned
    return db.value.export(checked.value, target, plan["format"])


def _transform_python(state: ETLAgentSchema, plan: dict, target: Path) -> Result[dict]:
    db = open_source(state.source_id)
    if not db:
        return db

    work = outputs_dir() / ".work" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        # The sandbox gets a CSV copy of the one table, so it needs no database access at all.
        exported = db.value.export(f"SELECT * FROM {quote_ident(plan['table'])}", work / "input.csv", "csv")
        if not exported:
            return exported

        failures: list[str] = []
        for _ in range(1 + MAX_CODE_REPAIRS):
            code = _strip_code_fences(text_of(pick_llm("high").invoke(_code_prompt(state, plan, failures))))
            ran = run_generated_code(code, work)
            if ran and (work / "output.csv").is_file():
                break
            failures.append(ran.error if not ran else "The code ran but did not write output.csv.")
        else:
            return Result.fail("etl.code_failed", f"{len(failures)} attempts failed. Last error: {failures[-1]}")

        converted = convert_file(work / "output.csv", target, plan["format"])
        return Result.ok({"path": str(target), **converted})
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _code_prompt(state: ETLAgentSchema, plan: dict, failures: list[str]) -> str:
    previous = ""
    if failures:
        previous = "Your previous code failed. Fix it.\n" + "\n".join(
            f"attempt {i} -> {f[-800:]}" for i, f in enumerate(failures, 1))
    return f"""
    You are a Python data analyst. Write pandas code that performs the transformation
    the user asked for. Output only code, no explanation and no markdown fences.

    Read the data with pd.read_csv("input.csv"). It holds every row of table {plan['table']}.
    Write the result with df.to_csv("output.csv", index=False).

    The code runs in an isolated process with no environment variables and no network
    access. Do not import os, subprocess or socket. Use only pandas and plain Python.
    Finish by printing a short summary of the result, for example its shape and head.

    User's request: {state.user_request}

    Tables:
    {state.schema_context}

    {previous}
    """


def _strip_code_fences(code: str) -> str:
    code = (code or "").strip()
    if not code.startswith("```"):
        return code

    lines = code.splitlines()
    lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]

    if lines and lines[0].strip().lower() in {"python", "py"}:
        lines = lines[1:]

    return "\n".join(lines).strip()


def write_output(state: ETLAgentSchema) -> dict:

    path = Path(state.output["path"])
    table = add_output(path)
    if not table:
        return {"error_code": table.code, "error_detail": table.error}

    o = state.output
    answer = (
        f"Saved {table.value} ({o['rows']:,} rows, {len(o['columns'])} columns) to {path}. "
        "It is now a table in the workspace source."
    )
    return {"output": o | {"table": table.value}, "final_answer": answer}


def report_failure(state: ETLAgentSchema) -> dict:

    headline = _FAILURE_MESSAGE.get(state.error_code, "The request could not be completed.")
    answer = f"{headline}\n\nDetail ({state.error_code}): {state.error_detail}"

    if len(state.errors) > 1:
        tried = "\n".join(f"  attempt {e['attempt']}: {e['detail']}" for e in state.errors)
        answer += f"\n\nTried {len(state.errors)} times:\n{tried}"

    return {"final_answer": answer}


def _retry_or_fail(state: ETLAgentSchema) -> str:
    if state.error_code in _RETRYABLE and state.attempts < MAX_ETL_ATTEMPTS:
        return "plan_etl"
    return "report_failure"


def context_edge(state: ETLAgentSchema) -> str:
    return "report_failure" if state.error_code else "plan_etl"


def plan_edge(state: ETLAgentSchema) -> str:
    return _retry_or_fail(state) if state.error_code else "run_etl"


def run_edge(state: ETLAgentSchema) -> str:
    return _retry_or_fail(state) if state.error_code else "write_output"


def write_edge(state: ETLAgentSchema) -> str:
    return "report_failure" if state.error_code else "end"


etl_analyst_graph = StateGraph(ETLAgentSchema)

etl_analyst_graph.add_node("read_context", read_context)
etl_analyst_graph.add_node("plan_etl", plan_etl)
etl_analyst_graph.add_node("run_etl", run_etl)
etl_analyst_graph.add_node("write_output", write_output)
etl_analyst_graph.add_node("report_failure", report_failure)

etl_analyst_graph.add_edge(START, "read_context")
etl_analyst_graph.add_conditional_edges(
    "read_context", context_edge, {"plan_etl": "plan_etl", "report_failure": "report_failure"},
)
etl_analyst_graph.add_conditional_edges(
    "plan_etl", plan_edge,
    {"plan_etl": "plan_etl", "run_etl": "run_etl", "report_failure": "report_failure"},
)
etl_analyst_graph.add_conditional_edges(
    "run_etl", run_edge,
    {"plan_etl": "plan_etl", "write_output": "write_output", "report_failure": "report_failure"},
)
etl_analyst_graph.add_conditional_edges(
    "write_output", write_edge, {"report_failure": "report_failure", "end": END},
)
etl_analyst_graph.add_edge("report_failure", END)

etl_analyst = etl_analyst_graph.compile()


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png
    from utils.sources import workspace

    save_graph_png(etl_analyst, "etl_analyst_graph.png")

    response = etl_analyst.invoke({
        "user_request": "fetch https://pokeapi.co/api/v2/pokemon?limit=50 and save it as pokemon",
        "source_id": workspace()["id"],
    })
    print(response["final_answer"])
