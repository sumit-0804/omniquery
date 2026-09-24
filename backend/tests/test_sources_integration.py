import pytest

from utils import sources
from utils.config import db_config

pytestmark = pytest.mark.integration


def _url(schema_db=None):
    c = db_config()
    return f"host={c['host']} port={c['port']} user={c['user']} password={c['password']} dbname={c['dbname']}"


def test_check_reports_the_demo_database():
    result = sources.check_postgres(_url())

    assert result, result.error
    assert result.value["tables"] == 5
    assert result.value["version"]
    # The demo login owns the tables, so it can write.
    assert result.value["readonly"] is False


def test_a_missing_schema_is_refused():
    assert sources.check_postgres(_url(), schema="no_such_schema").code == "source.no_schema"


def test_a_saved_postgres_source_answers_through_the_agent(monkeypatch):
    import agents.sql_analyst as sa
    from tests.fakes import FakeLLM

    assert sources.add_postgres("demo", _url())
    llm = FakeLLM(["SELECT count(*) FROM rides WHERE status = 'cancelled'", "1,925 rides were cancelled."])
    monkeypatch.setattr(sa, "pick_llm", lambda *a, **k: llm)

    state = sa.sql_analyst.invoke({"user_question": "how many rides were cancelled?", "source_id": "demo"})

    assert state["result_rows"] == [[1925]]
    assert "Postgres SQL query" in llm.prompts[0]
