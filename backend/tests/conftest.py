import pytest

from tests.fakes import FakeDB, FakeLLM
from utils.result import Result


def _postgres_up() -> bool:
    from utils.config import db_config
    from utils.database import DatabaseUtil

    try:
        return bool(DatabaseUtil(db_config()).execute_sql("SELECT 1"))
    except Exception:
        return False


def _laya_cached() -> bool:
    # Scan rather than try_to_load_from_cache: Laya pins a revision, so there is no refs/main.
    from huggingface_hub import scan_cache_dir

    from utils.laya_router import LAYA_MODEL

    try:
        return any(r.repo_id == LAYA_MODEL for r in scan_cache_dir().repos)
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    checks = {
        "integration": (_postgres_up, "Postgres with the demo data is not reachable"),
        "laya": (_laya_cached, "Laya weights are not downloaded yet"),
    }
    for marker, (available, reason) in checks.items():
        marked = [item for item in items if marker in item.keywords]
        if marked and not available():
            for item in marked:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    # A test that reaches a real provider spends quota and passes or fails on the network.
    import agents.data_agent
    import agents.etl_analyst
    import agents.sql_analyst

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to call a real LLM; patch pick_llm with a fake")

    for module in (agents.sql_analyst, agents.etl_analyst, agents.data_agent):
        monkeypatch.setattr(module, "pick_llm", refuse)


@pytest.fixture(autouse=True)
def _sources_in_tmp(tmp_path, monkeypatch):
    # Never read or write the user's real ~/.omniquery.
    monkeypatch.setenv("OMNIQUERY_HOME", str(tmp_path / "omniquery"))


@pytest.fixture
def fake_agent(monkeypatch):
    """Patch the SQL agent's LLM and database. Returns a function that sets the script."""
    import agents.sql_analyst as sa

    def setup(replies, execute=(), explain=()) -> FakeLLM:
        llm = FakeLLM(replies)
        FakeDB.execute_script = list(execute)
        FakeDB.explain_script = list(explain)
        FakeDB.executed = []
        FakeDB.explained = []
        monkeypatch.setattr(sa, "pick_llm", lambda *a, **k: llm)
        monkeypatch.setattr(sa, "open_source", lambda source_id: Result.ok(FakeDB(), notes={}))
        return llm

    return setup
