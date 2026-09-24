from agents.sql_analyst import MAX_SQL_ATTEMPTS, ROW_LIMIT, sql_analyst
from tests.fakes import FakeDB, query_failed, rows
from utils.result import Result

BAD = "SELECT r.fare_amount FROM rides r"
GOOD = "SELECT count(*) FROM rides"
GOOD_RUN = f"{GOOD}\nLIMIT {ROW_LIMIT}"  # the read-only check adds the LIMIT
MISSING_COLUMN = query_failed("42703", "column r.fare_amount does not exist")


def ask(question="how many rides?"):
    return sql_analyst.invoke({"user_question": question, "source_id": "demo"})


def test_explain_catches_a_bad_query_before_it_ever_runs(fake_agent):
    llm = fake_agent(
        replies=[BAD, GOOD, "There are 20,000 rides."],
        explain=[MISSING_COLUMN, Result.ok(None)],
        execute=[rows(["count"], [[20000]])],
    )
    state = ask()

    assert state["sql_attempts"] == 2
    assert state["final_answer"] == "There are 20,000 rides."
    assert state["error_code"] == ""
    assert [e["code"] for e in state["sql_errors"]] == ["42703"]
    assert FakeDB.executed == [GOOD_RUN]
    assert not llm.replies


def test_the_retry_prompt_carries_the_error_and_the_failed_sql(fake_agent):
    llm = fake_agent(
        replies=[BAD, GOOD, "answer"],
        explain=[MISSING_COLUMN],
        execute=[rows(["count"], [[1]])],
    )
    ask()

    first_gen, retry_gen = [p for p in llm.prompts if "Convert the user's question" in p]
    assert "previous attempts failed" not in first_gen
    assert "42703: column r.fare_amount does not exist" in retry_gen
    assert BAD in retry_gen


def test_it_gives_up_after_the_attempt_limit(fake_agent):
    fake_agent(replies=[BAD] * MAX_SQL_ATTEMPTS, explain=[MISSING_COLUMN] * MAX_SQL_ATTEMPTS)
    state = ask()

    assert state["sql_attempts"] == MAX_SQL_ATTEMPTS
    assert state["error_code"] == "db.query_failed"
    assert len(state["sql_errors"]) == MAX_SQL_ATTEMPTS
    assert f"Tried {MAX_SQL_ATTEMPTS} times" in state["final_answer"]
    assert FakeDB.executed == []


def test_a_query_that_plans_but_fails_at_runtime_is_retried_too(fake_agent):
    fake_agent(
        replies=["SELECT 1 / 0", GOOD, "answer"],
        execute=[query_failed("22012", "division by zero"), rows(["count"], [[1]])],
    )
    state = ask()

    assert state["sql_attempts"] == 2
    assert [e["code"] for e in state["sql_errors"]] == ["22012"]


def test_an_unreachable_database_is_not_retried(fake_agent):
    fake_agent(replies=[GOOD], explain=[Result.fail("db.unreachable", "connection refused")])
    state = ask()

    assert state["sql_attempts"] == 1
    assert state["error_code"] == "db.unreachable"


def test_a_refused_write_is_not_retried(fake_agent):
    fake_agent(
        replies=[GOOD],
        execute=[Result.fail("db.write_rejected", "cannot execute INSERT in a read-only transaction")],
    )
    state = ask()

    assert state["sql_attempts"] == 1
    assert state["error_code"] == "db.write_rejected"


def test_a_write_is_canceled_without_touching_the_database(fake_agent):
    llm = fake_agent(replies=["UPDATE rides SET fare = 0"])
    state = ask("set every fare to zero")

    assert state["is_safe"] == "No"
    assert "UPDATE" in state["comments"]
    assert "not run" in state["final_answer"]
    assert FakeDB.explained == [] and FakeDB.executed == []
    assert state["sql_attempts"] == 1
    assert not llm.replies


def test_the_executed_sql_carries_the_added_limit(fake_agent):
    fake_agent(replies=[GOOD, "answer"], execute=[rows(["count"], [[1]])])
    state = ask()

    assert state["generated_sql_query"] == GOOD_RUN
    assert FakeDB.explained == [GOOD_RUN]


def test_an_empty_result_is_answered_without_the_llm(fake_agent):
    llm = fake_agent(replies=[GOOD], execute=[rows(["count"], [])])
    state = ask()

    assert state["final_answer"] == "The query ran successfully but returned no rows."
    assert not llm.replies


def test_the_answer_prompt_sees_at_most_the_row_limit(fake_agent):
    data = [[i] for i in range(ROW_LIMIT + 5)]
    llm = fake_agent(replies=[GOOD, "answer"], execute=[rows(["n"], data)])
    state = ask()

    assert "...and 5 more rows" in llm.prompts[-1]
    assert state["result_row_count"] == ROW_LIMIT + 5


def test_a_plain_question_costs_two_llm_calls(fake_agent):
    llm = fake_agent(replies=[GOOD, "answer"], execute=[rows(["count"], [[1]])])
    ask()

    assert len(llm.prompts) == 2
