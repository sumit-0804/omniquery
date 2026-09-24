import re

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from Models.schema import AgentSchema
from utils.database import format_rows, notes_in, render_schema
from utils.llm_pick import pick_llm, text_of
from utils.result import Result
from utils.sources import open_source
from utils.sql_guard import check_readonly

# One number for the prompt's default LIMIT, the LIMIT added when the SQL has none,
# the rows the model reads, and the UI copy.
ROW_LIMIT = 25
MAX_SQL_ATTEMPTS = 3

# Above this many tables, one cheap LLM call picks the relevant ones first, so a large
# database does not flood the prompt. At or below it, every table is sent as is.
TABLE_SELECTION_THRESHOLD = 15

_FAILURE_MESSAGE = {
    "db.unreachable": "I could not reach the database, so I have no answer for you.",
    "db.query_failed": "The database rejected the generated query.",
    "db.write_rejected": "The generated query tried to modify data. The connection is read-only, so it was refused.",
    "db.schema_failed": "I could not read the database schema, so I could not build a query.",
    "source.not_found": "That data source is not saved, so I have nothing to query.",
    "source.empty": "This source has no tables yet. Add files, connect a database, or extract some data first.",
}

_DIALECT_NAME = {"postgres": "Postgres", "duckdb": "DuckDB"}


def prompt_query_context(state: AgentSchema) -> dict:

    db = open_source(state.source_id)
    if not db:
        return {"error_code": db.code, "error_detail": db.error}
    catalog = db.value.schema_catalog()
    if not catalog:
        return {"error_code": catalog.code, "error_detail": catalog.error}

    names = list(catalog.value["tables"])
    if not names:
        return {"error_code": "source.empty", "error_detail": f"{state.source_id} has no tables yet."}
    selected = (
        names if len(names) <= TABLE_SELECTION_THRESHOLD
        else select_tables(state.user_question, catalog.value)
    )
    notes = db.meta.get("notes") or {}

    return {
        "sql_dialect": db.value.dialect,
        "schema_context": render_schema(catalog.value, selected, notes),
        "selected_tables": selected,
        "notes_used": notes_in(catalog.value, selected, notes),
    }


def select_tables(question: str, catalog: dict) -> list[str]:
    tables = catalog["tables"]
    lines = []
    for name, t in tables.items():
        about = t["comment"] or ", ".join(c["name"] for c in t["columns"][:8])
        links = f"  (links to: {', '.join(t['references'])})" if t["references"] else ""
        lines.append(f"- {name}: {about}{links}")
    table_list = "\n".join(lines)

    prompt = f"""
    Pick the database tables needed to answer the question, including any needed for joins.
    Reply with table names only, one per line, and nothing else.

    Question: {question}

    Tables:
    {table_list}
    """

    try:
        reply = text_of(pick_llm("low").invoke(prompt))
    except Exception:
        # Selection only trims the prompt; losing it must not lose the answer.
        return list(tables)

    picked = _parse_table_names(reply, tables)
    if not picked:
        return list(tables)

    # Add the tables the picked ones point to, so a lookup needed for a join is not lost.
    for name in list(picked):
        picked.update(tables[name]["references"])
    return [name for name in tables if name in picked]


def _parse_table_names(reply: str, tables: dict) -> set[str]:
    known = {name.lower(): name for name in tables}
    found = set()
    for token in re.split(r"[\s,]+", reply):
        name = token.strip("`'\"*-.:;()[]").split(".")[-1].lower()
        if name in known:
            found.add(known[name])
    return found


def _failures_block(errors: list[dict]) -> str:
    if not errors:
        return ""
    lines = ["Your previous attempts failed. Fix the query."]
    for e in errors:
        lines.append(f"attempt {e['attempt']} -> {e['detail']}\n  SQL: {e['sql']}")
    lines.append("Return only the corrected SQL.")
    return "\n".join(lines)


def generate_sql(state: AgentSchema) -> dict:

    llm = pick_llm("medium")

    prompt = f"""
    You are an SQL analyst agent. Convert the user's question into one
    {_DIALECT_NAME.get(state.sql_dialect, state.sql_dialect)} SQL query that can be executed
    directly on the database. You are given the schema details, including table names,
    column names, data types, sample rows and notes written by the user.
    Unless the user asks for a specific number of rows, limit the output to {state.row_limit} rows.
    Output only the raw SQL statement. No explanation, no markdown, no backticks.
    {_chart_grain(state.row_limit)}

    User's question: {state.user_question}

    Database schema:
    {state.schema_context}

    {_failures_block(state.sql_errors)}
    """

    generated = _strip_sql_fences(text_of(llm.invoke(prompt)))

    # Clearing the error matters: a stale one would send a successful retry to report_failure.
    return {
        "generated_sql_query": generated,
        "sql_attempts": state.sql_attempts + 1,
        "error_code": "",
        "error_detail": "",
    }


def _chart_grain(row_limit: int) -> str:
    # Only callers that need more than ROW_LIMIT rows (the chart route) get this guidance.
    if row_limit <= ROW_LIMIT:
        return ""
    return f"""
    The result feeds a chart, so return the points to draw, not raw rows:
    - For a trend over time, group by a time unit that gives at most {row_limit} points
      (day, then week, then month as the date range grows).
    - For a comparison of categories, return the top 25 by value, sorted.
    - Return one row per point: the label or date column first, then the value column(s).
    """


def _strip_sql_fences(sql: str) -> str:
    sql = (sql or "").strip()
    if not sql.startswith("```"):
        return sql

    lines = sql.splitlines()
    lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]

    if lines and lines[0].strip().lower() in {"sql", "postgresql", "postgres"}:
        lines = lines[1:]

    return "\n".join(lines).strip()


def readonly_check(state: AgentSchema) -> dict:

    result = check_readonly(state.generated_sql_query, state.row_limit, dialect=state.sql_dialect)
    if not result:
        return {"is_safe": "No", "comments": result.error}

    # The checked SQL may carry an added LIMIT, so it replaces the generated text.
    return {"is_safe": "Yes", "comments": "", "generated_sql_query": result.value}


def canceled_sql(state: AgentSchema) -> dict:

    final_answer = f"The query was not run because it is not read-only: {state.comments}."

    return {"final_answer": final_answer, "messages": [AIMessage(content=final_answer)]}


def _failure(state: AgentSchema, result: Result) -> dict:
    code = result.error.split(":", 1)[0] if result.code == "db.query_failed" else result.code
    return {
        "error_code": result.code,
        "error_detail": result.error,
        "sql_errors": [{
            "attempt": state.sql_attempts,
            "code": code,
            "detail": result.error,
            "sql": state.generated_sql_query,
        }],
    }


def validate_sql(state: AgentSchema) -> dict:

    db = open_source(state.source_id)
    result = db.value.explain_sql(state.generated_sql_query) if db else db
    return {} if result else _failure(state, result)


def execute_sql(state: AgentSchema) -> dict:

    db = open_source(state.source_id)
    result = db.value.execute_sql(state.generated_sql_query) if db else db
    if not result:
        return _failure(state, result)

    data = result.value
    update = {
        "result_columns": data["columns"],
        "result_rows": data["rows"],
        "result_row_count": data["row_count"],
        "result_truncated": data["truncated"],
    }

    if data["row_count"] == 0:
        # Answered without the LLM, so it cannot invent rows that were not returned.
        answer = "The query ran successfully but returned no rows."
        update.update(final_answer=answer, messages=[AIMessage(content=answer)])

    return update


def report_failure(state: AgentSchema) -> dict:

    headline = _FAILURE_MESSAGE.get(state.error_code, "The request could not be completed.")
    answer = f"{headline}\n\nDetail ({state.error_code}): {state.error_detail}"

    if len(state.sql_errors) > 1:
        tried = "\n".join(f"  attempt {e['attempt']}: {e['detail']}" for e in state.sql_errors)
        answer += f"\n\nTried {len(state.sql_errors)} times:\n{tried}"

    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def represent_final_answer(state: AgentSchema) -> dict:

    llm = pick_llm("low")

    prompt = f"""
    You are an SQL analyst agent. Answer the user's question using the query result below.
    Be concise and clear. Do not include SQL or technical details.
    Only state what the result supports. Do not invent numbers.

    Query result:
    {format_rows(state.result_columns, state.result_rows, ROW_LIMIT)}

    User's question: {state.user_question}
    """

    llm_response = text_of(llm.invoke(prompt))

    return {"final_answer": llm_response, "messages": [AIMessage(content=llm_response)]}


def _retry_or_fail(state: AgentSchema) -> str:
    # Only a rejected query can improve on another try; an unreachable database
    # or a refused write fails the same way every time.
    if state.error_code == "db.query_failed" and state.sql_attempts < MAX_SQL_ATTEMPTS:
        return "generate_sql"
    return "report_failure"


def schema_edge(state: AgentSchema) -> str:
    return "report_failure" if state.error_code else "generate_sql"


def readonly_edge(state: AgentSchema) -> str:
    return "validate_sql" if state.is_safe == "Yes" else "canceled_sql"


def validation_edge(state: AgentSchema) -> str:
    return _retry_or_fail(state) if state.error_code else "execute_sql"


def build_graph(answer_node: str, answer_fn):
    """The SQL pipeline with `answer_fn` as its last step. The chart agent reuses it."""

    def execution_edge(state: AgentSchema) -> str:
        if state.error_code:
            return _retry_or_fail(state)
        # An empty result already produced its own answer.
        return "end" if state.final_answer else answer_node

    graph = StateGraph(AgentSchema)

    graph.add_node("prompt_query_context", prompt_query_context)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("check_readonly", readonly_check)
    graph.add_node("canceled_sql", canceled_sql)
    graph.add_node("validate_sql", validate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("report_failure", report_failure)
    graph.add_node(answer_node, answer_fn)

    graph.add_edge(START, "prompt_query_context")
    graph.add_conditional_edges(
        "prompt_query_context", schema_edge,
        {"report_failure": "report_failure", "generate_sql": "generate_sql"},
    )
    graph.add_edge("generate_sql", "check_readonly")
    graph.add_conditional_edges(
        "check_readonly", readonly_edge,
        {"validate_sql": "validate_sql", "canceled_sql": "canceled_sql"},
    )
    graph.add_conditional_edges(
        "validate_sql", validation_edge,
        {"generate_sql": "generate_sql", "report_failure": "report_failure", "execute_sql": "execute_sql"},
    )
    graph.add_conditional_edges(
        "execute_sql", execution_edge,
        {"generate_sql": "generate_sql", "report_failure": "report_failure", answer_node: answer_node, "end": END},
    )
    graph.add_edge("canceled_sql", END)
    graph.add_edge("report_failure", END)
    graph.add_edge(answer_node, END)

    return graph.compile()


sql_analyst = build_graph("represent_final_answer", represent_final_answer)


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png
    from utils.sources import default_source

    save_graph_png(sql_analyst, "sql_analyst_graph.png")

    response = sql_analyst.invoke({
        "user_question": "different types of payment method",
        "source_id": default_source().value["id"],
    })
    print(response["final_answer"])
