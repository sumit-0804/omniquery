import subprocess
import sys

import pytest
from langchain_core.messages import HumanMessage

import agents.data_agent as da
from Models.schema import DataAgentSchema


def _state(**update):
    return DataAgentSchema(messages=[HumanMessage(content="how many rides?")], source_id="demo", **update)


@pytest.fixture
def router(monkeypatch):
    """Script Jev's decision and the LLM router's answer; record LLM calls."""
    llm_calls = []

    def setup(decision=None, error=None, llm_answer="etl"):
        def fake_decide(message, questions):
            if error:
                raise error
            return decision

        def fake_llm_route(message):
            llm_calls.append(message)
            if llm_answer == "unclear":
                return {"route_response": "", "route_source": "llm",
                        "clarify_question": "Query it or save it to a file?", "clarify_why": "Could be either."}
            return {"route_response": llm_answer, "route_confidence": 1.0, "route_source": "llm"}

        monkeypatch.setattr(da, "decide", fake_decide)
        monkeypatch.setattr(da, "llm_route", fake_llm_route)
        return llm_calls

    return setup


@pytest.fixture
def fake_agents(monkeypatch):
    """Replace every agent graph with one that answers with its own name."""
    import dataclasses

    from agents.registry import REGISTRY

    class Graph:
        def __init__(self, key):
            self.key = key

        def invoke(self, _):
            return {"final_answer": f"answered by {self.key}"}

    for key, spec in list(REGISTRY.items()):
        monkeypatch.setitem(REGISTRY, key, dataclasses.replace(
            spec, graph=Graph(key), extract_answer=lambda r: r["final_answer"]))


def _run(message="how many rides?", thread="t1"):
    config = {"configurable": {"thread_id": thread}}
    return da.data_agent.invoke({"messages": [HumanMessage(content=message)], "source_id": "demo"}, config), config


def test_a_very_sure_jev_routes_without_the_llm(router):
    llm_calls = router({"route": "sql", "confidence": 0.93})
    update = da.router_node(_state())

    assert update["route_source"] == "jev"
    assert llm_calls == []
    assert da.route_edge(_state(**update)) == "sql_node"


def test_an_unsure_jev_hands_over_to_the_llm(router):
    llm_calls = router({"route": "sql", "confidence": 0.67}, llm_answer="etl")
    update = da.router_node(_state())

    assert update["route_source"] == "llm"
    assert llm_calls == ["how many rides?"]
    assert da.route_edge(_state(**update)) == "etl_node"


def test_a_jev_failure_falls_back_to_the_llm(router):
    llm_calls = router(error=RuntimeError("401 no OpenRouter key"), llm_answer="sql")
    update = da.router_node(_state())

    assert update["route_source"] == "llm"
    assert len(llm_calls) == 1


def test_a_clearly_ambiguous_request_asks_the_user_without_the_llm(router, fake_agents):
    llm_calls = router({"route": "ambiguous", "confidence": 0.93})
    paused, _ = _run(message="fix it", thread="t8")

    prompt = paused["__interrupt__"][0].value
    assert llm_calls == []
    assert [o["key"] for o in prompt["options"]] == ["sql", "etl", "chart"]
    assert "too vague" in prompt["why"]


def test_an_unsure_ambiguous_pick_still_goes_to_the_llm(router):
    llm_calls = router({"route": "ambiguous", "confidence": 0.55}, llm_answer="sql")
    update = da.router_node(_state())

    assert len(llm_calls) == 1
    assert update["route_response"] == "sql"


def test_an_unclear_request_pauses_and_resumes_with_the_users_choice(router, fake_agents):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    paused, config = _run()

    prompt = paused["__interrupt__"][0].value
    assert prompt["question"] == "Query it or save it to a file?"
    assert [o["key"] for o in prompt["options"]] == ["sql", "etl", "chart"]

    done = da.data_agent.invoke(Command(resume="etl"), config)
    assert done["messages"][-1].content == "answered by etl"
    assert done["route_source"] == "human"


def test_cancelling_the_question_ends_politely(router, fake_agents):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    _, config = _run(thread="t2")

    done = da.data_agent.invoke(Command(resume=""), config)
    assert "Ask again" in done["messages"][-1].content


@pytest.fixture
def explainer(monkeypatch):
    asked = []

    def fake_explain(request, question):
        asked.append(question)
        return f"plain words #{len(asked)}"

    monkeypatch.setattr(da, "explain_choice", fake_explain)
    return asked


def test_a_question_instead_of_a_choice_gets_an_explanation_then_the_same_options(router, fake_agents, explainer):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    _, config = _run(thread="t4")

    again = da.data_agent.invoke(Command(resume="what's the difference?"), config)
    prompt = again["__interrupt__"][0].value
    assert explainer == ["what's the difference?"]
    assert prompt["explanation"] == "plain words #1"
    assert [o["key"] for o in prompt["options"]] == ["sql", "etl", "chart"]

    done = da.data_agent.invoke(Command(resume="sql"), config)
    assert done["messages"][-1].content == "answered by sql"
    # The resume re-ran ask_human; the explanation must not have been asked for twice.
    assert explainer == ["what's the difference?"]


def test_choosing_chart_runs_the_chart_agent_and_passes_its_spec_up(router, fake_agents):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    _, config = _run(thread="t7")

    done = da.data_agent.invoke(Command(resume="3"), config)
    assert done["messages"][-1].content == "answered by chart"
    assert "chart_spec" in done


def test_a_reply_can_be_the_option_name(router, fake_agents):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    _, config = _run(thread="t5")

    assert da.data_agent.invoke(Command(resume=" ETL "), config)["messages"][-1].content == "answered by etl"


def test_after_too_many_questions_the_user_is_asked_to_rephrase(router, fake_agents, explainer):
    from langgraph.types import Command

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    _, config = _run(thread="t6")

    result = None
    for question in ["huh?", "still not sure", "which is better?"]:
        result = da.data_agent.invoke(Command(resume=question), config)

    assert "__interrupt__" not in result
    assert "start over" in result["messages"][-1].content
    assert len(explainer) == da.MAX_CLARIFY_ROUNDS - 1


def test_a_clear_request_runs_straight_through(router, fake_agents):
    router({"route": "sql", "confidence": 0.91})
    done, _ = _run(thread="t3")

    assert "__interrupt__" not in done
    assert done["messages"][-1].content == "answered by sql"


def test_the_cli_shows_the_question_and_resumes(router, fake_agents, monkeypatch, capsys):
    import main

    router({"route": "sql", "confidence": 0.6}, llm_answer="unclear")
    monkeypatch.setattr("builtins.input", lambda _: "2")

    assert main.ask("save the rides", "demo") == "answered by etl"
    out = capsys.readouterr().out
    assert "Query it or save it to a file?" in out and "2. etl" in out


def test_importing_the_agents_does_not_build_the_jev_client():
    code = "import sys, agents.data_agent, utils.jev_router as j; sys.exit(j._classifier is not None)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_the_route_question_offers_every_agent_and_ambiguous():
    criteria = da.ROUTE_QUESTIONS["route"].criteria

    assert list(criteria) == ["sql", "etl", "chart", "ambiguous"]


def test_decide_reads_jev_s_answer(monkeypatch):
    from types import SimpleNamespace

    import utils.jev_router as jr

    answer = SimpleNamespace(choices={"route": SimpleNamespace(choice="chart", confidence=0.91)})
    monkeypatch.setattr(jr, "_load", lambda: SimpleNamespace(invoke=lambda payload: answer))

    assert jr.decide("plot rides", da.ROUTE_QUESTIONS) == {"route": "chart", "confidence": 0.91}
