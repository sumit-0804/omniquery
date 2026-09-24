import pytest

from utils import sources
from utils.config import PROJECT_ROOT
from utils.database import MAX_FETCH_ROWS
from utils.duck import DuckDBUtil, build_database, table_name

DATA = PROJECT_ROOT / "data"


@pytest.fixture(scope="module")
def rides_db(tmp_path_factory):
    target = tmp_path_factory.mktemp("duck") / "data.duckdb"
    assert build_database(target, sorted(DATA.glob("*.csv")))
    return DuckDBUtil(target)


def test_each_file_becomes_a_table_with_counts_and_values(rides_db):
    tables = rides_db.schema_catalog().value["tables"]
    status = next(c for c in tables["rides"]["columns"] if c["name"] == "status")

    assert set(tables) == {"payments", "ratings", "rides", "users", "vehicles"}
    assert tables["rides"]["row_estimate"] == 20000
    assert status["values"] == ["cancelled", "completed", "in_progress", "requested"]
    assert status["values_complete"]


def test_generated_sql_cannot_read_other_files(rides_db):
    other = str(DATA / "users.csv").replace("\\", "/")

    for query in (f"SELECT * FROM read_csv('{other}')", f"SELECT * FROM '{other}'"):
        result = rides_db.execute_sql(query)
        assert result.code == "db.query_failed"
        assert "disabled by configuration" in result.error


def test_generated_sql_cannot_write(rides_db):
    result = rides_db.execute_sql("CREATE TABLE zzz (i int)")

    assert result.code == "db.query_failed"
    assert "read-only" in result.error


def test_explain_catches_a_wrong_column_without_running(rides_db):
    result = rides_db.explain_sql("SELECT r.fare_amount FROM rides r")

    assert result.code == "db.query_failed"
    assert result.error.startswith("Binder Error:")


def test_casts_and_literal_colons_run(rides_db):
    result = rides_db.execute_sql("SELECT requested_at::date AS d, 'a:b' AS s FROM rides LIMIT 1")

    assert result, result.error
    assert result.value["rows"][0][1] == "a:b"


def test_the_fetch_cap_holds(rides_db):
    result = rides_db.execute_sql(f"SELECT * FROM range({MAX_FETCH_ROWS + 10})")

    assert result.value["row_count"] == MAX_FETCH_ROWS
    assert result.value["truncated"] is True


def test_a_slow_query_is_interrupted(rides_db):
    slow = DuckDBUtil(rides_db.path, timeout_s=0.3)
    result = slow.execute_sql("SELECT sum(a.range * b.range) FROM range(200000) a, range(200000) b")

    assert result.code == "db.query_failed"
    assert result.error.startswith("Timeout:")


def test_a_missing_database_file_is_unreachable(tmp_path):
    assert DuckDBUtil(tmp_path / "gone.duckdb").execute_sql("SELECT 1").code == "db.unreachable"


def test_table_names_are_safe_and_unique():
    used: set[str] = set()

    assert [table_name(s, used) for s in ("Rides 2025", "rides-2025", "2025", "!!")] == [
        "rides_2025", "rides_2025_2", "t_2025", "table",
    ]


def test_add_files_copies_imports_and_saves(tmp_path):
    result = sources.add_files("Rides files", [str(DATA / "rides.csv"), str(DATA / "users.csv")])

    assert result, result.error
    assert result.value["tables"] == ["rides", "users"]
    folder = sources.uploads_dir("rides-files")
    assert (folder / "data.duckdb").is_file() and (folder / "rides.csv").is_file()

    opened = sources.open_source("rides-files")
    assert opened.value.dialect == "duckdb"
    assert opened.value.execute_sql("SELECT count(*) FROM rides").value["rows"] == [[20000]]


def test_a_file_duckdb_cannot_read_saves_nothing(tmp_path):
    broken = tmp_path / "broken.parquet"
    broken.write_text("this is not parquet")

    result = sources.add_files("broken", [str(broken)])

    assert result.code == "source.bad_file"
    assert sources.list_sources() == []
    assert not sources.uploads_dir("broken").exists()


def test_unsupported_files_are_refused(tmp_path):
    doc = tmp_path / "notes.docx"
    doc.write_text("x")

    assert sources.add_files("docs", [str(doc)]).code == "source.bad_file"


def test_removing_a_file_source_deletes_its_copies():
    sources.add_files("rides", [str(DATA / "vehicles.csv")])
    sources.remove_source("rides")

    assert not sources.uploads_dir("rides").exists()


def test_add_table_never_replaces_an_existing_table(tmp_path):
    from utils.duck import add_table

    db = tmp_path / "w.duckdb"
    assert add_table(db, DATA / "vehicles.csv", "vehicles").value == "vehicles"
    assert add_table(db, DATA / "vehicles.csv", "vehicles").value == "vehicles_2"


def test_export_writes_every_row_into_the_allowed_folder(rides_db, tmp_path):
    target = tmp_path / "all.parquet"
    result = rides_db.export("SELECT * FROM rides;", target, "parquet")

    assert result, result.error
    assert result.value["rows"] == 20000


def test_export_still_cannot_read_other_files(rides_db, tmp_path):
    other = str(DATA / "users.csv").replace("\\", "/")
    result = rides_db.export(f"SELECT * FROM '{other}'", tmp_path / "x.parquet", "parquet")

    assert result.code == "db.query_failed"
    assert "disabled by configuration" in result.error
