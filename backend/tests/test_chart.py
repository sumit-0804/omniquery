import json
from datetime import date, datetime
from decimal import Decimal

import pytest

import agents.chart_analyst as ca
from agents.sql_analyst import ROW_LIMIT, sql_analyst
from tests.fakes import FakeDB, rows
from utils.result import Result

SQL = "SELECT date_trunc('month', requested_at) AS month, count(*) AS rides FROM rides GROUP BY 1"
MONTHS = rows(["month", "rides"], [[date(2025, 1, 1), 800], [date(2025, 2, 1), 900], [date(2025, 3, 1), 850]])


def design(**fields):
    return json.dumps({"mark": "line", "x": "month", "y": "rides", "color": None,
                       "title": "Rides per month", "answer": "Rides peaked in February."} | fields)


@pytest.fixture
def chart(fake_agent, monkeypatch):
    """Same fakes as the SQL agent tests; the chart step shares the scripted LLM."""
    def setup(replies, execute=(), explain=()):
        llm = fake_agent(replies, execute=execute, explain=explain)
        monkeypatch.setattr(ca, "pick_llm", lambda *a, **k: llm)
        return llm
    return setup


def ask(question="plot rides per month"):
    return ca.chart_analyst.invoke({"user_question": question, "source_id": "demo", "row_limit": ca.CHART_ROW_LIMIT})


def test_a_chart_question_makes_two_llm_calls_and_returns_a_valid_spec(chart):
    llm = chart([SQL, design()], execute=[MONTHS])
    state = ask()

    assert len(llm.prompts) == 2
    assert state["final_answer"] == "Rides peaked in February."
    assert state["chart_spec"]["mark"]["type"] == "line"
    assert state["chart_spec"]["encoding"]["x"] == {"field": "month", "type": "temporal", "title": "month"}
    assert ca.validate_spec(state["chart_spec"]) == ""
    assert not state.get("chart_error")


def test_the_chart_route_asks_for_chart_sized_results(chart):
    llm = chart([SQL, design()], execute=[MONTHS])
    ask()

    assert FakeDB.executed == [f"{SQL}\nLIMIT {ca.CHART_ROW_LIMIT}"]
    assert "feeds a chart" in llm.prompts[0]


def test_the_sql_route_keeps_its_small_limit_and_prompt(fake_agent):
    llm = fake_agent([SQL, "answer"], execute=[MONTHS])
    sql_analyst.invoke({"user_question": "rides per month", "source_id": "demo"})

    assert FakeDB.executed == [f"{SQL}\nLIMIT {ROW_LIMIT}"]
    assert "feeds a chart" not in llm.prompts[0]


def test_a_cut_off_result_still_charts_and_says_so(chart):
    data = MONTHS.value | {"truncated": True}
    llm = chart([SQL, design()], execute=[Result.ok(data)])
    state = ask()

    assert state["chart_note"].startswith("Showing the first 3 rows")
    assert state["chart_spec"]["title"]["subtitle"] == state["chart_note"]
    assert "Say so in the answer" in llm.prompts[1]


def test_a_single_number_is_answered_not_charted(chart):
    llm = chart([SQL, "There were 1,925 cancelled rides."], execute=[rows(["count"], [[1925]])])
    state = ask("chart the number of cancelled rides")

    assert state["chart_error"] == "chart.unsupported_shape"
    assert state.get("chart_spec") is None
    assert state["final_answer"] == "There were 1,925 cancelled rides."
    assert len(llm.prompts) == 2


def test_an_unknown_column_falls_back_to_a_heuristic_but_keeps_the_answer(chart):
    llm = chart([SQL, design(x="made_up", answer="February was busiest.")], execute=[MONTHS])
    state = ask()

    assert state["chart_spec"]["encoding"]["x"]["field"] == "month"
    assert state["final_answer"] == "February was busiest."
    assert len(llm.prompts) == 2


def test_a_reply_that_is_not_json_gets_a_heuristic_chart_and_one_answer_call(chart):
    llm = chart([SQL, "Sure, here is a lovely chart!", "Rides rose from 800 to 900."], execute=[MONTHS])
    state = ask()

    assert state["chart_spec"]["mark"]["type"] == "line"
    assert state["final_answer"] == "Rides rose from 800 to 900."
    assert len(llm.prompts) == 3


def test_a_spec_the_schema_rejects_keeps_the_answer_and_rows(chart, monkeypatch):
    chart([SQL, design()], execute=[MONTHS])
    monkeypatch.setattr(ca, "build_spec", lambda *a, **k: {"mark": 5})
    state = ask()

    assert state["chart_error"] == "chart.invalid_spec"
    assert state.get("chart_spec") is None
    assert state["chart_error_detail"]
    assert state["final_answer"] == "Rides peaked in February."
    assert len(state["result_rows"]) == 3


@pytest.mark.parametrize("columns, row, mark", [
    (["month", "rides"], [date(2025, 1, 1), 5], "line"),
    (["method", "revenue"], ["card", Decimal("10.5")], "bar"),
    (["distance", "fare"], [3.2, 11.0], "point"),
    (["month", "rides"], ["2025-01", 5], "line"),
])
def test_heuristic_marks_follow_the_column_types(columns, row, mark):
    kinds = ca.column_kinds(columns, [row, row])

    assert ca.heuristic_choice(columns, kinds)["mark"] == mark


def test_values_are_json_ready():
    cols = ["at", "amount"]
    data = [[datetime(2025, 1, 1, 8, 30), Decimal("12.50")], [datetime(2025, 1, 2, 9, 0), Decimal("3")]]
    kinds = ca.column_kinds(cols, data)
    spec = ca.build_spec(cols, data, kinds, ca.heuristic_choice(cols, kinds))

    assert spec["data"]["values"][0] == {"at": "2025-01-01T08:30:00", "amount": 12.5}
    json.dumps(spec)


def test_float_noise_is_rounded_away():
    spec = ca.build_spec(["m", "v"], [["a", 198738.88000000006], ["b", 1.0]], {"m": "nominal", "v": "quantitative"},
                         {"mark": "bar", "x": "m", "y": "v", "color": None, "title": ""})

    assert spec["data"]["values"][0]["v"] == 198738.88


def test_many_categories_become_a_horizontal_bar_sorted_by_value():
    cols = ["driver", "rides"]
    data = [[f"driver {i}", i] for i in range(ca.MAX_BAR_CATEGORIES + 5)]
    kinds = ca.column_kinds(cols, data)
    spec = ca.build_spec(cols, data, kinds, {"mark": "bar", "x": "driver", "y": "rides", "color": None, "title": ""})

    assert spec["encoding"]["y"]["field"] == "driver"
    assert spec["encoding"]["y"]["sort"] == "-x"
    assert spec["encoding"]["x"]["field"] == "rides"
    assert ca.validate_spec(spec) == ""


def test_few_categories_stay_vertical_and_sorted():
    cols = ["method", "revenue"]
    data = [["card", 10], ["cash", 5]]
    spec = ca.build_spec(cols, data, ca.column_kinds(cols, data),
                         {"mark": "bar", "x": "method", "y": "revenue", "color": None, "title": ""})

    assert spec["encoding"]["x"] == {"field": "method", "type": "nominal", "title": "method", "sort": "-y"}


def test_the_chart_graph_ends_in_design_chart():
    graph = ca.chart_analyst.get_graph()
    edges = {(e.source, e.target) for e in graph.edges}

    assert "represent_final_answer" not in graph.nodes
    assert ("execute_sql", "design_chart") in edges
    assert ("design_chart", "__end__") in edges
