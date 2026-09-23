from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from Models.schema import AgentSchema, JudgeSchema
from utils.database import DatabaseUtil
from utils.llm_pick import pick_llm, text_of

_FAILURE_MESSAGE = {
    "db.unreachable": "I could not reach the database, so I have no answer for you.",
    "db.query_failed": "The database rejected the generated query.",
    "db.write_rejected": "The generated query tried to modify data. The connection is read-only, so it was refused.",
    "db.schema_failed": "I could not read the database schema, so I could not build a query.",
}


def curate_ques(state: AgentSchema) -> dict:

    llm = pick_llm("low")

    response = text_of(llm.invoke(f"""
    You are a helpful SQL analyst assistant. Rephrase the user's question into a more
    specific question that can be used to generate a SQL query. Only remove ambiguous
    words and phrases. Do not answer the question.
    Here is the user's question: {state.user_question}
    """))

    return {"curated_ques": response, "messages": [HumanMessage(content=response)]}


def prompt_query_context(state: AgentSchema) -> dict:

    schema = DatabaseUtil().schema_details("public")
    if not schema:
        return {"error_code": schema.code, "error_detail": schema.error}

    prompt = f"""
    You are an SQL analyst agent. Convert the user's natural language query into a
    Postgres SQL query that can be executed directly on the database. You are given the
    schema details including table names, column names, data types and sample rows.
    Unless the user asks for a specific number of rows, limit the output to 10 rows.
    Output only the raw SQL statement. No explanation, no markdown, no backticks.

    User's Query: {state.curated_ques}

    Database Schema Details:
    {schema.value}
    """

    return {"prompt_query_context": prompt}


def generate_sql(state: AgentSchema) -> dict:

    llm = pick_llm("medium")
    generated = _strip_sql_fences(text_of(llm.invoke(state.prompt_query_context)))

    return {"generated_sql_query": generated}


def _strip_sql_fences(sql: str) -> str:
    sql = (sql or "").strip()
    if not sql.startswith("```"):
        return sql

    lines = sql.splitlines()
    lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]

    if lines and lines[0].strip().lower() in {"sql", "postgresql", "postgres"}:
        lines = lines[1:]

    return "\n".join(lines).strip()


def is_safe_sql(state: AgentSchema) -> dict:

    llm = pick_llm("medium")
    llm_judge = llm.with_structured_output(JudgeSchema, method="json_schema")

    prompt = f"""
    You are an SQL judge for data security. Decide whether this SQL query is safe.
    It must only read data. Answer 'No' if it contains INSERT, UPDATE, DELETE, DROP,
    ALTER, TRUNCATE, CREATE or anything else that changes the database.
    Respond 'Yes' if it is safe, otherwise 'No', and explain your decision.
    SQL query: {state.generated_sql_query}
    """

    response = llm_judge.invoke(prompt).model_dump()

    return {"is_safe": response["answer"], "comments": response["comments"]}


def canceled_sql(state: AgentSchema) -> dict:

    final_answer = (
        f"The generated SQL query was judged unsafe to execute. "
        f"Reason: {state.comments}. The query was not run."
    )

    return {"final_answer": final_answer, "messages": [AIMessage(content=final_answer)]}


def execute_sql(state: AgentSchema) -> dict:

    result = DatabaseUtil().execute_sql(state.generated_sql_query)
    if not result:
        return {"error_code": result.code, "error_detail": result.error}

    if result.meta.get("row_count", 0) == 0:
        # Answered without the LLM, so it cannot invent rows that were not returned.
        answer = "The query ran successfully but returned no rows."
        return {
            "sql_query_execution_result": "",
            "final_answer": answer,
            "messages": [AIMessage(content=answer)],
        }

    return {"sql_query_execution_result": result.value}


def report_failure(state: AgentSchema) -> dict:

    headline = _FAILURE_MESSAGE.get(state.error_code, "The request could not be completed.")
    answer = f"{headline}\n\nDetail ({state.error_code}): {state.error_detail}"

    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}


def represent_final_answer(state: AgentSchema) -> dict:

    llm = pick_llm("low")

    prompt = f"""
    You are an SQL analyst agent. Answer the user's question using the query result below.
    Be concise and clear. Do not include SQL or technical details.
    Only state what the result supports. Do not invent numbers.

    Query result:
    {state.sql_query_execution_result}

    User's question: {state.curated_ques}
    """

    llm_response = text_of(llm.invoke(prompt))

    return {"final_answer": llm_response, "messages": [AIMessage(content=llm_response)]}


sql_agent_graph = StateGraph(AgentSchema)

sql_agent_graph.add_node("curate_ques", curate_ques)
sql_agent_graph.add_node("prompt_query_context", prompt_query_context)
sql_agent_graph.add_node("generate_sql", generate_sql)
sql_agent_graph.add_node("is_safe_sql", is_safe_sql)
sql_agent_graph.add_node("canceled_sql", canceled_sql)
sql_agent_graph.add_node("execute_sql", execute_sql)
sql_agent_graph.add_node("report_failure", report_failure)
sql_agent_graph.add_node("represent_final_answer", represent_final_answer)

sql_agent_graph.add_edge(START, "curate_ques")
sql_agent_graph.add_edge("curate_ques", "prompt_query_context")


def schema_edge(state: AgentSchema) -> str:
    return "report_failure" if state.error_code else "generate_sql"


sql_agent_graph.add_conditional_edges(
    "prompt_query_context", schema_edge,
    {"report_failure": "report_failure", "generate_sql": "generate_sql"},
)

sql_agent_graph.add_edge("generate_sql", "is_safe_sql")


def is_safe_sql_edge(state: AgentSchema) -> str:
    return "execute_sql" if state.is_safe.lower() == "yes" else "canceled_sql"


sql_agent_graph.add_conditional_edges(
    "is_safe_sql", is_safe_sql_edge,
    {"execute_sql": "execute_sql", "canceled_sql": "canceled_sql"},
)


def execution_edge(state: AgentSchema) -> str:
    if state.error_code:
        return "report_failure"
    # An empty result already produced its own answer.
    return "end" if state.final_answer else "represent_final_answer"


sql_agent_graph.add_conditional_edges(
    "execute_sql", execution_edge,
    {
        "report_failure": "report_failure",
        "represent_final_answer": "represent_final_answer",
        "end": END,
    },
)

sql_agent_graph.add_edge("canceled_sql", END)
sql_agent_graph.add_edge("report_failure", END)
sql_agent_graph.add_edge("represent_final_answer", END)

sql_analyst = sql_agent_graph.compile()


if __name__ == "__main__":
    from utils.graph_viz import save_graph_png

    save_graph_png(sql_analyst, "sql_analyst_graph.png")

    response = sql_analyst.invoke({"user_question": "different types of payment method"})
    print(response["final_answer"])
