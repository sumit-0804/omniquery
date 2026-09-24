import pytest

from utils.sql_guard import check_readonly


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM rides",
    "WITH c AS (SELECT city FROM users) SELECT city, count(*) FROM c GROUP BY city",
    "SELECT 1 UNION SELECT 2",
    "SELECT requested_at::date, 'a:b' FROM rides",
])
def test_read_only_queries_are_allowed(sql):
    assert check_readonly(sql, limit=25)


@pytest.mark.parametrize("sql, reason", [
    ("UPDATE rides SET fare = 0", "UPDATE"),
    ("DELETE FROM rides", "DELETE"),
    ("INSERT INTO rides (ride_id) VALUES (1)", "INSERT"),
    ("DROP TABLE rides", "DROP"),
    ("TRUNCATE rides", "TRUNCATE"),
    ("SELECT * INTO rides_copy FROM rides", "SELECT INTO"),
    ("SELECT 1; DROP TABLE rides", "2 statements"),
    ("VACUUM rides", "VACUUM"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT pg_terminate_backend(123)", "pg_terminate_backend"),
])
def test_writes_and_dangerous_calls_are_refused_with_a_precise_reason(sql, reason):
    result = check_readonly(sql, limit=25)

    assert not result
    assert result.code == "sql.not_readonly"
    assert reason in result.error


def test_a_write_hidden_inside_a_select_is_caught():
    result = check_readonly("WITH d AS (DELETE FROM rides RETURNING *) SELECT * FROM d", limit=25)

    assert not result
    assert "DELETE" in result.error


def test_a_limit_is_added_when_missing_without_rewriting_the_sql():
    result = check_readonly("SELECT requested_at::date FROM rides;", limit=25)

    assert result.value == "SELECT requested_at::date FROM rides\nLIMIT 25"
    assert result.meta["limit_added"] is True


@pytest.mark.parametrize("sql", [
    "SELECT * FROM rides LIMIT 5",
    "SELECT * FROM rides FETCH FIRST 5 ROWS ONLY",
])
def test_an_existing_limit_is_left_alone(sql):
    result = check_readonly(sql, limit=25)

    assert result.value == sql
    assert result.meta["limit_added"] is False


def test_a_trailing_comment_cannot_swallow_the_added_limit():
    result = check_readonly("SELECT * FROM rides -- all of them", limit=25)

    assert result.value.endswith("\nLIMIT 25")


def test_unparseable_sql_is_passed_through_for_explain_to_judge():
    sql = "SELECT FROM WHERE ((("
    result = check_readonly(sql, limit=25)

    assert result
    assert result.value == sql
    assert result.meta["checked"] is False


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('C:/secrets.csv')",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT * FROM read_json_auto('x.json')",
    "SELECT * FROM glob('*')",
])
def test_duckdb_file_readers_are_refused(sql):
    result = check_readonly(sql, 25, dialect="duckdb")

    assert result.code == "sql.not_readonly"


def test_file_readers_are_only_denied_for_duckdb():
    # In Postgres these names mean nothing special; EXPLAIN will reject them.
    assert check_readonly("SELECT * FROM read_csv('x')", 25)


def test_no_limit_is_added_when_the_caller_wants_every_row():
    assert check_readonly("SELECT * FROM rides", None).value == "SELECT * FROM rides"
    assert check_readonly("DELETE FROM rides", None).code == "sql.not_readonly"
