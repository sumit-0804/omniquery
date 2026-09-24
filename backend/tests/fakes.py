from typing import ClassVar

from langchain_core.messages import AIMessage

from utils.result import Result


class FakeLLM:
    """Scripted replies in call order. Records every prompt it was sent."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(str(prompt))
        if not self.replies:
            raise AssertionError(f"unexpected LLM call:\n{prompt}")
        return AIMessage(content=self.replies.pop(0))


class FakeDB:
    """Stands in for DatabaseUtil. explain_sql and execute_sql return scripted Results
    in order; an empty explain script means every query plans fine."""

    explain_script: ClassVar[list[Result]] = []
    execute_script: ClassVar[list[Result]] = []
    explained: ClassVar[list[str]] = []
    executed: ClassVar[list[str]] = []

    dialect = "postgres"

    def __init__(self, *args, **kwargs):
        pass

    def schema_catalog(self):
        return Result.ok(tiny_catalog())

    def explain_sql(self, query):
        FakeDB.explained.append(query)
        return FakeDB.explain_script.pop(0) if FakeDB.explain_script else Result.ok(None)

    def execute_sql(self, query):
        FakeDB.executed.append(query)
        return FakeDB.execute_script.pop(0)


def column(name, col_type="integer", pk=False, fk=None, values=None, complete=True, comment=None):
    return {"name": name, "type": col_type, "comment": comment, "pk": pk, "fk": fk,
            "values": values, "values_complete": complete}


def table(columns, references=(), comment=None, rows_estimate=None, samples=()):
    return {"comment": comment, "row_estimate": rows_estimate, "columns": list(columns),
            "references": list(references), "samples": list(samples)}


def tiny_catalog(schema_name="public"):
    return {"schema": schema_name, "tables": {
        "rides": table([column("ride_id", pk=True), column("status", "character varying")]),
    }}


def rows(columns: list[str], data: list[list]) -> Result:
    return Result.ok({"columns": columns, "rows": data, "row_count": len(data), "truncated": False})


def query_failed(pgcode: str, message: str) -> Result:
    return Result.fail("db.query_failed", f"{pgcode}: {message}")
