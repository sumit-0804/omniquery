import pytest

from utils.config import db_config
from utils.database import MAX_FETCH_ROWS, DatabaseUtil

pytestmark = pytest.mark.integration


def db(schema="public"):
    return DatabaseUtil(db_config(), schema=schema)


def test_a_wrong_column_reports_the_real_pgcode():
    result = db().execute_sql("SELECT r.fare_amount FROM rides r")

    assert result.code == "db.query_failed"
    assert result.error.startswith("42703:")


def test_postgres_itself_refuses_writes():
    result = db().execute_sql("CREATE TABLE zzz_probe (i int)")

    assert result.code == "db.write_rejected"


def test_casts_and_literal_colons_run():
    result = db().execute_sql(
        "SELECT requested_at::date AS d, 'a:b' AS s FROM rides ORDER BY ride_id LIMIT 1"
    )

    assert result, result.error
    assert result.value["rows"][0][1] == "a:b"


def test_the_fetch_cap_holds_on_a_real_cursor():
    result = db().execute_sql(f"SELECT g FROM generate_series(1, {MAX_FETCH_ROWS + 10}) g")

    assert result.value["row_count"] == MAX_FETCH_ROWS
    assert result.value["truncated"] is True


def test_explain_catches_a_wrong_column_without_running_the_query():
    result = db().explain_sql("SELECT r.fare_amount FROM rides r")

    assert result.code == "db.query_failed"
    assert result.error.startswith("42703:")


def test_explain_passes_a_valid_query():
    assert db().explain_sql("SELECT count(*) FROM rides WHERE status = 'cancelled'")


def test_the_catalog_has_keys_and_every_status_value():
    from utils.database import clear_schema_cache

    clear_schema_cache()
    rides = db().schema_catalog().value["tables"]["rides"]
    by_name = {c["name"]: c for c in rides["columns"]}

    assert by_name["ride_id"]["pk"]
    assert by_name["rider_id"]["fk"] == "users.user_id"
    assert by_name["status"]["values"] == ["cancelled", "completed", "in_progress", "requested"]
    assert by_name["status"]["values_complete"]


def test_schema_sets_the_search_path():
    assert db().execute_sql("SELECT count(*) FROM rides")

    missing = db("no_such_schema").execute_sql("SELECT count(*) FROM rides")
    assert missing.error.startswith("42P01:")


def test_export_streams_every_row_with_real_types(tmp_path):
    import duckdb

    target = tmp_path / "rides.parquet"
    result = db().export("SELECT * FROM rides", target, "parquet")

    assert result.value["rows"] == 20000
    types = {name: kind for name, kind, *_ in duckdb.sql(f"DESCRIBE SELECT * FROM '{target.as_posix()}'").fetchall()}
    assert types["fare"] == "DOUBLE"
    assert types["requested_at"] == "TIMESTAMP"
