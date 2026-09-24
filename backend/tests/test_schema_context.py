from decimal import Decimal

import pytest

import agents.sql_analyst as sa
from tests.fakes import FakeLLM, column, table
from utils.database import render_schema


def rideshare():
    return {"schema": "public", "tables": {
        "rides": table(
            [column("ride_id", pk=True), column("rider_id", fk="users.user_id"),
             column("fare", "numeric(10,2)"),
             column("status", "character varying(30)", values=["cancelled", "completed"])],
            references=["users"], rows_estimate=20000,
            samples=[(1, 7, Decimal("12.50"), "completed")],
        ),
        "users": table(
            [column("user_id", pk=True),
             column("city", "character varying", values=["Ottawa", "Toronto"], complete=False)],
            comment="riders and drivers",
        ),
    }}


def test_render_shows_keys_values_estimates_and_plain_samples():
    text = render_schema(rideshare())

    assert "Table: rides (~20,000 rows)" in text
    assert "ride_id integer PK" in text
    assert "rider_id integer -> users.user_id" in text
    assert "values: cancelled, completed" in text
    assert "1 | 7 | 12.50 | completed" in text
    assert "Decimal(" not in text
    assert "Table: users  -- riders and drivers" in text


def test_an_incomplete_value_list_is_not_presented_as_complete():
    text = render_schema(rideshare())

    assert "sample values (may be incomplete): Ottawa, Toronto" in text


def test_render_can_be_limited_to_some_tables():
    text = render_schema(rideshare(), tables=["users"])

    assert "Table: users" in text
    assert "Table: rides" not in text


def big_catalog(n=20):
    tables = {f"decoy_{i}": table([column("id", pk=True)]) for i in range(n)}
    tables["users"] = table([column("user_id", pk=True)])
    tables["rides"] = table([column("ride_id", pk=True), column("rider_id", fk="users.user_id")],
                            references=["users"])
    return {"schema": "public", "tables": tables}


@pytest.fixture
def selector(monkeypatch):
    def setup(reply):
        llm = FakeLLM([reply])
        monkeypatch.setattr(sa, "pick_llm", lambda *a, **k: llm)
        return llm
    return setup


def _context_for(catalog, monkeypatch, notes=None):
    from Models.schema import AgentSchema
    from utils.result import Result

    class DB:
        dialect = "postgres"

        def schema_catalog(self):
            return Result.ok(catalog)

    monkeypatch.setattr(sa, "open_source", lambda source_id: Result.ok(DB(), notes=notes or {}))
    return sa.prompt_query_context(AgentSchema(user_question="how many rides?", source_id="demo"))


def test_small_schemas_skip_selection_entirely(selector, monkeypatch):
    llm = selector("unused")
    catalog = {"schema": "public", "tables": {f"t{i}": table([column("id")])
                                               for i in range(sa.TABLE_SELECTION_THRESHOLD)}}

    update = _context_for(catalog, monkeypatch)

    assert llm.prompts == []
    assert update["selected_tables"] == list(catalog["tables"])


def test_large_schemas_select_with_one_llm_call(selector, monkeypatch):
    llm = selector("rides")

    update = _context_for(big_catalog(), monkeypatch)

    assert len(llm.prompts) == 1
    assert update["selected_tables"] == ["users", "rides"]
    assert "decoy_0" not in update["schema_context"]


def test_selection_keeps_picked_tables_and_adds_what_they_point_to(selector):
    selector("rides")

    assert sa.select_tables("how many rides?", big_catalog()) == ["users", "rides"]


def test_selection_ignores_unknown_names_and_tolerates_formatting(selector):
    selector("- public.rides\n* `made_up_table`\n1. users,")

    assert set(sa.select_tables("q", big_catalog())) == {"users", "rides"}


def test_a_useless_reply_falls_back_to_every_table(selector):
    selector("I am not sure which tables you mean.")

    assert len(sa.select_tables("q", big_catalog())) == 22


def test_an_llm_failure_falls_back_to_every_table(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("all providers down")

    class Broken:
        invoke = broken

    monkeypatch.setattr(sa, "pick_llm", lambda *a, **k: Broken())

    assert len(sa.select_tables("q", big_catalog())) == 22


def test_the_selection_prompt_lists_links_between_tables(selector):
    llm = selector("rides")
    sa.select_tables("q", big_catalog())

    assert "- rides: ride_id, rider_id  (links to: users)" in llm.prompts[0]
