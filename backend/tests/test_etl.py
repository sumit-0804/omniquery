import json

import duckdb
import pytest

import agents.etl_analyst as etl
from tests.fakes import FakeLLM
from utils import sources
from utils.config import PROJECT_ROOT

DATA = PROJECT_ROOT / "data"


@pytest.fixture
def llm(monkeypatch):
    def setup(*replies):
        fake = FakeLLM([r if isinstance(r, str) else json.dumps(r) for r in replies])
        monkeypatch.setattr(etl, "pick_llm", lambda *a, **k: fake)
        return fake
    return setup


@pytest.fixture
def rides_files():
    assert sources.add_files("rides-files", [str(DATA / "rides.csv"), str(DATA / "users.csv")])
    return "rides-files"


@pytest.fixture
def fake_api(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"count": 3, "results": [
                {"name": "bulbasaur", "url": "u1", "types": ["grass", "poison"]},
                {"name": "charmander", "url": "u4", "types": ["fire"]},
                {"name": "squirtle", "url": "u7", "types": ["water"]},
            ]}

    monkeypatch.setattr("utils.etl_tools.requests.get", lambda url, timeout: Response())


def run(request, source_id):
    return etl.etl_analyst.invoke({"user_request": request, "source_id": source_id})


def _sql(query):
    return duckdb.sql(query).fetchall()


def _rows(path):
    return _sql(f"SELECT count(*) FROM '{path.as_posix()}'")[0][0]


def test_an_extract_saves_a_file_and_a_workspace_table_with_one_llm_call(llm, fake_api):
    fake = llm({"kind": "extract", "url": "https://pokeapi.co/api/v2/pokemon", "name": "pokemon"})
    state = run("fetch pokemon", sources.workspace()["id"])

    assert state["error_code"] == ""
    assert _rows(sources.outputs_dir() / "pokemon.parquet") == 3
    assert state["output"]["table"] == "pokemon"
    assert "Saved pokemon (3 rows, 3 columns)" in state["final_answer"]
    assert len(fake.prompts) == 1

    tables = sources.open_source("workspace").value.schema_catalog().value["tables"]
    assert "pokemon" in tables


def test_nested_values_are_stored_as_json_text(llm, fake_api):
    llm({"kind": "extract", "url": "https://x.test/p", "name": "pokemon"})
    run("fetch pokemon", "workspace")

    path = (sources.outputs_dir() / "pokemon.parquet").as_posix()
    assert _sql(f"SELECT types FROM '{path}' ORDER BY name")[0][0] == '["grass", "poison"]'


def test_outputs_never_overwrite_each_other(llm, fake_api):
    llm({"kind": "extract", "url": "https://x.test/p", "name": "pokemon"},
        {"kind": "extract", "url": "https://x.test/p", "name": "pokemon"})
    first, second = run("fetch", "workspace"), run("fetch again", "workspace")

    assert first["output"]["table"] == "pokemon"
    assert second["output"]["table"] == "pokemon_2"
    assert {o["name"] for o in sources.list_outputs()} == {"pokemon.parquet", "pokemon_2.parquet"}


def test_a_sql_transform_saves_every_row(llm, rides_files):
    llm({"kind": "transform", "engine": "sql", "name": "cancelled_rides",
         "sql": "SELECT * FROM rides WHERE status = 'cancelled'"})
    state = run("save the cancelled rides", rides_files)

    assert state["output"]["rows"] == 1925
    assert _rows(sources.outputs_dir() / "cancelled_rides.parquet") == 1925


def test_the_user_can_ask_for_csv(llm, rides_files):
    llm({"kind": "transform", "engine": "sql", "name": "cities", "format": "csv",
         "sql": "SELECT city, count(*) AS n FROM users GROUP BY city"})
    state = run("csv of users per city", rides_files)

    assert state["output"]["path"].endswith("cities.csv")
    assert (sources.outputs_dir() / "cities.csv").read_text().startswith("city,n")


def test_a_failing_query_is_retried_with_the_error(llm, rides_files):
    fake = llm({"kind": "transform", "engine": "sql", "name": "fares", "sql": "SELECT fare_amount FROM rides"},
               {"kind": "transform", "engine": "sql", "name": "fares", "sql": "SELECT fare FROM rides"})
    state = run("save all fares", rides_files)

    assert state["attempts"] == 2
    assert state["output"]["rows"] == 20000
    assert "Binder Error" in fake.prompts[1]
    assert "fare_amount" in fake.prompts[1]


def test_a_write_is_refused_and_not_retried(llm, rides_files):
    llm({"kind": "transform", "engine": "sql", "name": "x", "sql": "DELETE FROM rides"})
    state = run("delete all rides", rides_files)

    assert state["error_code"] == "sql.not_readonly"
    assert state["attempts"] == 1
    assert "not read-only" in state["final_answer"]


def test_a_plan_that_is_not_json_gives_up_after_three_tries(llm, rides_files):
    llm("sure, I will do that", "ok", "{not json")
    state = run("do something", rides_files)

    assert state["error_code"] == "etl.bad_plan"
    assert state["attempts"] == etl.MAX_ETL_ATTEMPTS
    assert "Tried 3 times" in state["final_answer"]


def test_the_pandas_fallback_repairs_its_own_code(llm, rides_files):
    fake = llm(
        {"kind": "transform", "engine": "python", "table": "users", "name": "user_ids", "why": "string work"},
        "raise ValueError('first try is wrong')",
        "import pandas as pd\n"
        "df = pd.read_csv('input.csv')\n"
        "df[['user_id']].to_csv('output.csv', index=False)\n"
        "print(df.shape)",
    )
    state = run("keep only the user ids", rides_files)

    assert state["error_code"] == ""
    assert state["output"]["columns"] == ["user_id"]
    assert state["output"]["rows"] == _rows(sources.uploads_dir("rides-files") / "users.csv")
    assert len(fake.prompts) == 3
    assert "first try is wrong" in fake.prompts[2]
    assert not list((sources.outputs_dir() / ".work").iterdir())


def test_an_unknown_source_fails_before_any_llm_call(llm):
    fake = llm()
    state = run("anything", "nope")

    assert state["error_code"] == "source.not_found"
    assert fake.prompts == []


def test_the_graph_matches_the_agreed_steps():
    edges = {(e.source, e.target) for e in etl.etl_analyst.get_graph().edges}

    for step in [("__start__", "read_context"), ("read_context", "plan_etl"), ("plan_etl", "run_etl"),
                 ("run_etl", "write_output"), ("write_output", "__end__"), ("run_etl", "plan_etl")]:
        assert step in edges, step
