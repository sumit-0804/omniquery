from contextlib import contextmanager

import psycopg2

from utils import database
from utils.database import MAX_FETCH_ROWS, DatabaseUtil, format_rows

CONFIG = {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d"}


def test_format_rows_caps_and_says_how_many_were_left_out():
    text = format_rows(["n"], [[i] for i in range(30)], limit=25)
    lines = text.splitlines()

    assert lines[0] == "n"
    assert len(lines) == 1 + 25 + 1
    assert lines[-1] == "...and 5 more rows"


def test_format_rows_under_the_limit_has_no_footer():
    text = format_rows(["a", "b"], [[1, None], [2, "x"]], limit=25)
    assert text == "a | b\n1 | \n2 | x"


class _Cursor:
    def __init__(self, total, description=(("n",),), error=None):
        self.total, self.description, self.error = total, description, error

    def execute(self, query):
        if self.error:
            raise self.error

    def fetchmany(self, size):
        return [(i,) for i in range(min(self.total, size))]


def _db_with(cursor, monkeypatch) -> DatabaseUtil:
    db = DatabaseUtil(CONFIG)

    @contextmanager
    def fake_cursor(readonly=True):
        yield cursor

    monkeypatch.setattr(db, "_cursor", fake_cursor)
    return db


def test_execute_sql_returns_structured_rows(monkeypatch):
    result = _db_with(_Cursor(total=3), monkeypatch).execute_sql("SELECT n")

    assert result.value == {"columns": ["n"], "rows": [[0], [1], [2]], "row_count": 3, "truncated": False}


def test_execute_sql_stops_at_the_fetch_cap_and_flags_it(monkeypatch):
    result = _db_with(_Cursor(total=MAX_FETCH_ROWS + 500), monkeypatch).execute_sql("SELECT n")

    assert result.value["row_count"] == MAX_FETCH_ROWS
    assert result.value["truncated"] is True


def test_query_errors_use_the_code_colon_message_shape(monkeypatch):
    error = psycopg2.errors.UndefinedColumn("column r.fare does not exist")
    result = _db_with(_Cursor(total=0, error=error), monkeypatch).execute_sql("SELECT r.fare")

    assert result.code == "db.query_failed"
    assert result.error.split(": ", 1)[1] == "column r.fare does not exist"


def test_error_mapping_covers_unreachable_and_read_only():
    assert database._query_error(psycopg2.OperationalError("refused")).code == "db.unreachable"
    assert database._query_error(psycopg2.errors.ReadOnlySqlTransaction("ro")).code == "db.write_rejected"
