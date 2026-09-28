import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import agents.chart_analyst as ca
import agents.data_agent as da
import agents.etl_analyst as etl
import agents.sql_analyst as sa
from api.app import create_app
from api.events import label
from tests.fakes import FakeDB, query_failed, rows
from utils import sources
from utils.config import PROJECT_ROOT
from utils.result import Result

SQL = "SELECT count(*) FROM rides WHERE status = 'cancelled'"


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def demo_source():
    record = sources._record("demo", "demo", "postgres", url="postgresql://u:secret@h:5432/d",
                             schema="public", readonly=True)
    sources._save({"demo": record})


@pytest.fixture
def agents(monkeypatch, demo_source):
    """Route with a scripted Jev pick; answer with a model that streams like a real one."""
    def setup(route="sql", replies=(), execute=(), explain=(), confidence=0.95):
        model = GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))
        for module in (sa, ca, etl):
            monkeypatch.setattr(module, "pick_llm", lambda *a, **k: model)
        monkeypatch.setattr(da, "decide", lambda m, q: {"route": route, "confidence": confidence})
        monkeypatch.setattr(sa, "open_source", lambda sid: Result.ok(FakeDB(), notes={}))
        FakeDB.execute_script, FakeDB.explain_script = list(execute), list(explain)
        FakeDB.executed, FakeDB.explained = [], []
    return setup


def events_of(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def ask(client, q="how many rides were cancelled?", source="demo"):
    return events_of(client.get("/api/ask", params={"q": q, "source": source}))


def steps(events, status="done"):
    return [e["id"] for e in events if e["type"] == "step" and e["status"] == status]


def types(events):
    return [e["type"] for e in events]


def before(events, first, second):
    kinds = types(events)
    return kinds.index(first) < kinds.index(second)


def test_a_sql_question_streams_its_steps_payloads_and_answer(client, agents):
    agents(replies=[SQL, "There were 1,925 cancelled rides."], execute=[rows(["count"], [[1925]])])
    events = ask(client)

    ran = steps(events)
    for node in ["submitted", "router_node", "prompt_query_context", "generate_sql",
                 "check_readonly", "validate_sql", "execute_sql", "represent_final_answer"]:
        assert node in ran
    assert ran.index("generate_sql") < ran.index("execute_sql") < ran.index("represent_final_answer")

    routing = next(e for e in events if e["type"] == "routing")
    assert routing == routing | {"agent": "sql", "source": "jev"}
    assert next(e for e in events if e["type"] == "sql")["query"] == SQL
    assert next(e for e in events if e["type"] == "judge")["safe"] is True
    assert next(e for e in events if e["type"] == "rows")["rows"] == [[1925]]
    assert before(events, "sql", "rows") and before(events, "rows", "answer")

    chunks = [e for e in events if e["type"] == "answer" and not e["done"]]
    assert len(chunks) > 1  # streamed word by word
    assert "".join(e["text"] for e in chunks) == "There were 1,925 cancelled rides."
    assert events[-1] == events[-1] | {"type": "done", "outcome": "answer"}


def test_every_event_has_a_time_that_never_goes_back(client, agents):
    agents(replies=[SQL, "answer"], execute=[rows(["count"], [[1]])])
    times = [e["t"] for e in ask(client)]

    assert times == sorted(times)


def test_every_step_is_active_before_it_is_done(client, agents):
    agents(replies=[SQL, "answer"], execute=[rows(["count"], [[1]])])
    events = [e for e in ask(client) if e["type"] == "step" and e["id"] != "submitted"]

    for i, e in enumerate(events):
        if e["status"] == "done":
            assert any(p["id"] == e["id"] and p["status"] == "active" for p in events[:i])


def test_a_failed_explain_emits_a_retry_and_a_second_query(client, agents):
    agents(replies=["SELECT fare_amount FROM rides", SQL, "answer"], execute=[rows(["count"], [[1]])],
           explain=[query_failed("42703", "column fare_amount does not exist")])
    events = ask(client)

    retry = next(e for e in events if e["type"] == "retry")
    assert retry == retry | {"attempt": 1, "of": 3, "code": "42703"}
    assert [e["attempt"] for e in events if e["type"] == "sql"] == [1, 2]
    assert steps(events).count("generate_sql") == 2


def test_spent_retries_end_in_an_error_with_the_agents_headline(client, agents):
    failed = query_failed("42703", "column fare_amount does not exist")
    agents(replies=["SELECT fare_amount FROM rides"] * 3, explain=[failed] * 3)
    events = ask(client)

    error = next(e for e in events if e["type"] == "error")
    assert error == error | {"code": "db.query_failed", "attempts": 3,
                             "headline": "The database rejected the generated query."}
    assert events[-1]["outcome"] == "error"


def test_a_write_is_canceled_not_an_error(client, agents):
    agents(replies=["UPDATE rides SET fare = 0"])
    events = ask(client, "set every fare to zero")

    assert next(e for e in events if e["type"] == "judge")["safe"] is False
    assert "check_readonly" in steps(events, "failed")
    assert "error" not in types(events)
    assert events[-1]["outcome"] == "canceled"


def test_a_chart_question_streams_the_spec(client, agents):
    design = json.dumps({"mark": "bar", "x": "status", "y": "n", "title": "Rides", "answer": "Most completed."})
    agents(route="chart", replies=[SQL, design], execute=[rows(["status", "n"], [["completed", 9], ["cancelled", 2]])])
    events = ask(client, "chart rides by status")

    chart = next(e for e in events if e["type"] == "chart")
    assert chart["spec"]["mark"]["type"] == "bar"
    assert "represent_final_answer" not in steps(events)
    assert "".join(e["text"] for e in events if e["type"] == "answer") == "Most completed."


def test_an_etl_run_streams_its_output(client, agents, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"name": "bulbasaur"}, {"name": "ivysaur"}]

    monkeypatch.setattr("utils.etl_tools.requests.get", lambda url, timeout: Response())
    agents(route="etl", replies=[json.dumps({"kind": "extract", "url": "https://x.test/p", "name": "pokemon"})])
    events = ask(client, "fetch pokemon", source="workspace")

    assert ["read_context", "plan_etl", "run_etl", "write_output"] == [
        s for s in steps(events) if s in ("read_context", "plan_etl", "run_etl", "write_output")]
    output = next(e for e in events if e["type"] == "output")
    assert output == output | {"name": "pokemon.parquet", "table": "pokemon", "rows": 2}


def test_a_vague_question_pauses_and_resumes_on_the_same_run(client, agents, monkeypatch):
    agents(route="ambiguous", replies=[SQL, "There were 1,925."], execute=[rows(["count"], [[1925]])])
    monkeypatch.setattr(da, "explain_choice", lambda request, question: "sql shows it, etl saves it.")
    paused = ask(client, "fix it")

    clarify = next(e for e in paused if e["type"] == "clarify")
    assert [o["key"] for o in clarify["options"]] == ["sql", "etl", "chart"]
    assert paused[-1]["outcome"] == "clarify"
    thread = clarify["thread_id"]

    explained = events_of(client.get(f"/api/ask/{thread}/resume", params={"reply": "what's the difference?"}))
    assert next(e for e in explained if e["type"] == "clarify")["explanation"] == "sql shows it, etl saves it."

    done = events_of(client.get(f"/api/ask/{thread}/resume", params={"reply": "sql"}))
    assert next(e for e in done if e["type"] == "routing")["source"] == "human"
    assert done[-1]["outcome"] == "answer"
    assert "submitted" not in steps(done)


def test_resuming_a_run_that_is_not_paused_fails_cleanly(client):
    events = events_of(client.get("/api/ask/nope/resume", params={"reply": "sql"}))

    assert events[0] == events[0] | {"type": "error", "code": "run.not_paused"}


def test_an_unknown_source_is_an_error_event(client):
    events = ask(client, source="nope")

    assert events[0] == events[0] | {"type": "error", "code": "source.not_found"}
    assert events[-1]["outcome"] == "error"


def test_chat_returns_the_same_run_as_one_response(client, agents):
    agents(replies=[SQL, "There were 1,925 cancelled rides."], execute=[rows(["count"], [[1925]])])
    body = client.post("/api/chat", json={"q": "how many?", "source": "demo"}).json()

    assert body["answer"] == "There were 1,925 cancelled rides."
    assert body["outcome"] == "answer"


def test_unlisted_nodes_get_a_readable_label():
    assert label("generate_sql") == "Generating SQL"
    assert label("dedupe_rows") == "Dedupe rows"


def test_sources_never_expose_a_password(client, demo_source):
    listed = client.get("/api/sources").json()

    assert "secret" not in json.dumps(listed)
    assert listed[0]["url"] == "postgresql://u:****@h:5432/d"


def test_a_writable_source_carries_the_read_only_role_snippet(client, demo_source):
    assert "readonly_role_sql" not in client.get("/api/sources").json()[0]

    sources._save({"rw": sources._record("rw", "rw", "postgres", url="postgresql://u:p@h/d",
                                         schema="sales", readonly=False)})
    listed = client.get("/api/sources").json()[0]
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA sales" in listed["readonly_role_sql"]


def test_a_bad_connection_string_is_a_400(client):
    response = client.post("/api/sources/test", json={"dsn": "not a url", "schema": "public"})

    assert response.status_code == 400
    assert response.json()["code"] == "source.bad_url"


def test_an_uploaded_csv_becomes_a_source_with_a_catalog_and_notes(client):
    csv = (PROJECT_ROOT / "data" / "vehicles.csv").read_bytes()
    added = client.post("/api/sources/files", data={"name": "fleet"},
                        files=[("files", ("vehicles.csv", csv, "text/csv"))])
    assert added.status_code == 200, added.text
    assert added.json()["source"]["tables"] == ["vehicles"]

    client.put("/api/sources/fleet/notes", json={"path": "vehicles", "note": "one row per car"})
    catalog = client.get("/api/sources/fleet/catalog").json()
    vehicles = next(t for t in catalog["tables"] if t["name"] == "vehicles")
    assert vehicles["note"] == "one row per car"
    assert vehicles["row_estimate"] > 0
    assert client.post("/api/sources/fleet/refresh").json()["tables"] == 1


def test_an_upload_cannot_choose_where_it_lands(client):
    csv = b"a,b\n1,2\n"
    added = client.post("/api/sources/files", data={"name": "sneaky"},
                        files=[("files", ("../../evil.csv", csv, "text/csv"))])

    assert added.status_code == 200
    assert added.json()["source"]["files"] == ["evil.csv"]


def test_the_workspace_cannot_be_deleted_and_unknown_sources_are_404(client):
    sources.workspace()

    assert client.delete("/api/sources/workspace").status_code == 400
    assert client.delete("/api/sources/nope").status_code == 404


def test_outputs_download_only_listed_files(client):
    sources.workspace()
    (sources.outputs_dir() / "report.csv").write_bytes(b"a\n1\n")

    assert [o["name"] for o in client.get("/api/outputs").json()] == ["report.csv"]
    assert client.get("/api/outputs/report.csv").text == "a\n1\n"
    assert client.get("/api/outputs/..%2Fsources.json").status_code == 404
    assert client.get("/api/outputs/data.duckdb").status_code == 404


def test_health_and_providers_answer_without_network(client):
    assert client.get("/api/health").json()["ok"] is True
    assert isinstance(client.get("/api/providers").json(), list)
