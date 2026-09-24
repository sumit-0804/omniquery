from tests.fakes import column, table
from tests.test_schema_context import _context_for, big_catalog
from utils.database import notes_in, render_schema


def catalog():
    return {"schema": "public", "tables": {
        "rides": table([column("ride_id", pk=True), column("fare_amount", "numeric")]),
        "users": table([column("user_id", pk=True)]),
    }}


def test_notes_are_rendered_next_to_their_table_and_column():
    text = render_schema(catalog(), notes={"rides": "one row per trip", "rides.fare_amount": "in CAD"})

    assert "Table: rides  note: one row per trip" in text
    assert "fare_amount numeric  note: in CAD" in text


def test_only_notes_that_were_shown_are_reported():
    notes = {"rides.fare_amount": "in CAD", "users": "riders and drivers",
             "rides.gone_column": "stale", "missing_table": "stale"}

    assert notes_in(catalog(), None, notes) == ["rides.fare_amount", "users"]
    assert notes_in(catalog(), ["rides"], notes) == ["rides.fare_amount"]


def test_the_prompt_context_reports_the_notes_it_used(monkeypatch):
    update = _context_for(catalog(), monkeypatch, notes={"rides.fare_amount": "in CAD"})

    assert "note: in CAD" in update["schema_context"]
    assert update["notes_used"] == ["rides.fare_amount"]


def test_a_note_on_an_unselected_table_is_not_claimed(monkeypatch):
    import agents.sql_analyst as sa
    from tests.fakes import FakeLLM

    llm = FakeLLM(["rides"])
    monkeypatch.setattr(sa, "pick_llm", lambda *a, **k: llm)

    update = _context_for(big_catalog(), monkeypatch, notes={"decoy_3": "old", "rides": "trips"})

    assert update["notes_used"] == ["rides"]
    assert "old" not in update["schema_context"]
