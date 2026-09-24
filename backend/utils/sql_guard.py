import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from utils.result import Result

_WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.TruncateTable, exp.Command,
)

# Read-only in the transaction sense, so the read-only session does not stop them,
# but they read server files, kill sessions, change settings or reach other databases.
_DENIED_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "pg_terminate_backend", "pg_cancel_backend",
    "pg_reload_conf", "pg_rotate_logfile", "set_config", "dblink", "dblink_exec",
    "pg_sleep",
}

# DuckDB table functions that read files. External access is off at query time;
# this refuses them earlier and with a clearer reason.
_DENIED_DUCKDB_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "parquet_scan", "read_json",
    "read_json_auto", "read_ndjson", "read_text", "read_blob", "glob", "sniff_csv",
}

_LOOKS_LIKE_QUERY = re.compile(r"^\s*(\(\s*)*(select|with)\b", re.IGNORECASE)


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def check_readonly(sql: str, limit: int | None, dialect: str = "postgres") -> Result[str]:
    """Allow a single read-only query, adding a LIMIT if it has none (unless limit is None).

    Returns the SQL to run. `meta["checked"]` is False when sqlglot could not parse
    it: that is not proof of a write, so the SQL passes through for EXPLAIN to judge,
    and the read-only session remains the real guarantee.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except SqlglotError:
        return Result.ok(sql, checked=False, limit_added=False)

    if len(statements) != 1:
        return Result.fail("sql.not_readonly", f"found {len(statements)} statements; only one is allowed")

    denied = _DENIED_FUNCTIONS | (_DENIED_DUCKDB_FUNCTIONS if dialect == "duckdb" else set())
    root = statements[0]
    if isinstance(root, exp.Command) and _LOOKS_LIKE_QUERY.match(sql):
        # Unfamiliar syntax in what is still a query. Let Postgres decide.
        return Result.ok(sql, checked=False, limit_added=False)
    if not isinstance(root, exp.Query):
        return Result.fail("sql.not_readonly", f"found {_statement_name(root)} statement")

    for node in root.walk():
        if isinstance(node, _WRITE_NODES):
            return Result.fail("sql.not_readonly", f"found {_statement_name(node)} statement")
        if isinstance(node, exp.Into):
            return Result.fail("sql.not_readonly", "found SELECT INTO, which creates a table")
        if isinstance(node, exp.Func) and _function_name(node) in denied:
            return Result.fail("sql.not_readonly", f"found {_function_name(node)}(), which is not allowed")

    if limit is None or root.args.get("limit") is not None:
        return Result.ok(sql, checked=True, limit_added=False)

    # Appended as text rather than re-rendered, so the SQL stays what the model wrote.
    # The newline keeps a trailing "-- comment" from swallowing the LIMIT.
    return Result.ok(f"{sql.rstrip().rstrip(';').rstrip()}\nLIMIT {limit}", checked=True, limit_added=True)


def _statement_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        return (node.name or "unknown").upper()
    return {
        exp.TruncateTable: "TRUNCATE",
        exp.Merge: "MERGE",
    }.get(type(node), type(node).__name__.upper())
