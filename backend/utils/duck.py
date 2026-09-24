"""Uploaded files, queried through DuckDB.

Files are imported once into a DuckDB database file when the source is added. At query
time that file is opened read-only with file system access switched off, so generated
SQL can read the imported tables and nothing else on disk.
"""

import re
import threading
from collections.abc import Callable
from pathlib import Path

import duckdb

from utils.database import MAX_FETCH_ROWS, MAX_LISTED_VALUES, is_text
from utils.result import Result

_READERS = {
    ".csv": "read_csv_auto", ".tsv": "read_csv_auto", ".parquet": "read_parquet",
    ".json": "read_json_auto", ".jsonl": "read_json_auto", ".ndjson": "read_json_auto",
}
SUPPORTED = set(_READERS)

# Output formats for COPY; JSON is written one object per line.
COPY_OPTIONS = {"parquet": "(FORMAT parquet)", "csv": "(FORMAT csv, HEADER)", "json": "(FORMAT json)"}
EXPORT_TIMEOUT_S = 120.0

_CATALOG_CACHE: dict[tuple, dict] = {}


def clear_catalog_cache() -> None:
    _CATALOG_CACHE.clear()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def table_name(stem: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_") or "table"
    if base[0].isdigit():
        base = f"t_{base}"
    name, n = base, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def build_database(target: Path, files: list[Path]) -> Result[list[str]]:
    """One table per file. Nothing is left behind if any file fails to load."""
    building = target.with_suffix(".building")
    building.unlink(missing_ok=True)
    used: set[str] = set()
    tables = []
    conn = duckdb.connect(str(building))
    try:
        for f in files:
            name = table_name(f.stem, used)
            reader = _READERS[f.suffix.lower()]
            conn.execute(f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM {reader}({quote_literal(str(f))})")
            tables.append(name)
    except duckdb.Error as exc:
        conn.close()
        building.unlink(missing_ok=True)
        return Result.fail("source.bad_file", f"{f.name}: {str(exc).strip()}")
    conn.close()
    building.replace(target)
    return Result.ok(tables)


def _query_error(exc: duckdb.Error) -> Result:
    # Same `code: detail` shape as Postgres; DuckDB messages already start with "Binder Error: ...".
    message = str(exc).strip()
    if ":" not in message.split("\n", 1)[0]:
        message = f"{type(exc).__name__}: {message}"
    return Result.fail("db.query_failed", message)


def _posix_dir(folder: Path) -> str:
    return folder.resolve().as_posix().rstrip("/") + "/"


def write_frame(frame, target: Path, fmt: str) -> int:
    """Write a DataFrame with DuckDB (pandas alone would need pyarrow for Parquet). Returns rows."""
    conn = duckdb.connect()
    try:
        conn.register("frame", frame)
        return conn.execute(f"COPY frame TO {quote_literal(target.as_posix())} {COPY_OPTIONS[fmt]}").fetchone()[0]
    finally:
        conn.close()


def convert_file(source: Path, target: Path, fmt: str) -> dict:
    """Rewrite a file in another format. Returns {rows, columns}."""
    conn = duckdb.connect()
    try:
        select = f"SELECT * FROM {_READERS[source.suffix.lower()]}({quote_literal(source.as_posix())})"
        columns = [d[0] for d in conn.execute(f"{select} LIMIT 0").description]
        rows = conn.execute(f"COPY ({select}) TO {quote_literal(target.as_posix())} {COPY_OPTIONS[fmt]}").fetchone()[0]
        return {"rows": rows, "columns": columns}
    finally:
        conn.close()


def add_table(db_path: Path, file: Path, wanted: str) -> Result[str]:
    """Load a file into the database as a new table. Never replaces an existing one."""
    try:
        conn = duckdb.connect(str(db_path))
    except duckdb.Error as exc:
        return Result.fail("db.unreachable", str(exc).strip())
    try:
        used = {r[0] for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        name = table_name(wanted, used)
        reader = _READERS[file.suffix.lower()]
        conn.execute(f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM {reader}({quote_literal(file.as_posix())})")
    except duckdb.Error as exc:
        return Result.fail("source.bad_file", f"{file.name}: {str(exc).strip()}")
    finally:
        conn.close()
    return Result.ok(name)


class DuckDBUtil:
    dialect = "duckdb"

    def __init__(self, path: Path, timeout_s: float = 15.0):
        self.path = Path(path)
        self.timeout_s = timeout_s

    def _run(self, work: Callable, writable: Path | None = None, timeout_s: float | None = None) -> Result:
        """Run `work` on a read-only connection with file access off, except `writable` if given."""
        if not self.path.is_file():
            return Result.fail("db.unreachable", f"{self.path.name} is missing. Add the files again.")
        try:
            conn = duckdb.connect(str(self.path), read_only=True)
            # Must be set after connecting, and before access is switched off and locked.
            if writable is not None:
                conn.execute(f"SET allowed_directories = [{quote_literal(_posix_dir(writable))}]")
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
        except duckdb.Error as exc:
            return Result.fail("db.unreachable", str(exc).strip())

        # DuckDB has no statement_timeout, so interrupt from outside.
        limit = timeout_s or self.timeout_s
        timer = threading.Timer(limit, conn.interrupt)
        timer.start()
        try:
            return Result.ok(work(conn))
        except duckdb.InterruptException:
            return Result.fail("db.query_failed", f"Timeout: the query ran longer than {limit:g}s")
        except duckdb.Error as exc:
            return _query_error(exc)
        finally:
            timer.cancel()
            conn.close()

    def execute_sql(self, query: str) -> Result[dict]:
        def work(conn):
            cur = conn.execute(query)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(MAX_FETCH_ROWS + 1) if cur.description else []
            rows = [list(row) for row in fetched[:MAX_FETCH_ROWS]]
            return {"columns": columns, "rows": rows, "row_count": len(rows),
                    "truncated": len(fetched) > MAX_FETCH_ROWS}

        return self._run(work)

    def export(self, query: str, target: Path, fmt: str) -> Result[dict]:
        """Write every row of `query` to `target`. Only the target's folder is writable."""
        query = query.strip().rstrip(";")

        def work(conn):
            columns = [d[0] for d in conn.execute(f"SELECT * FROM ({query}) LIMIT 0").description]
            rows = conn.execute(
                f"COPY ({query}) TO {quote_literal(target.resolve().as_posix())} {COPY_OPTIONS[fmt]}"
            ).fetchone()[0]
            return {"path": str(target), "rows": rows, "columns": columns}

        return self._run(work, writable=target.parent, timeout_s=EXPORT_TIMEOUT_S)

    def explain_sql(self, query: str) -> Result[None]:
        def work(conn):
            conn.execute(f"EXPLAIN {query}")

        return self._run(work)

    def schema_catalog(self) -> Result[dict]:
        """Same shape as the Postgres catalog, so rendering and table selection are shared."""
        key = (str(self.path), self.path.stat().st_mtime_ns if self.path.is_file() else 0)
        if key in _CATALOG_CACHE:
            return Result.ok(_CATALOG_CACHE[key], cached=True)

        result = self._run(_read_catalog)
        if not result:
            return Result.fail("db.schema_failed", result.error) if result.code == "db.query_failed" else result
        _CATALOG_CACHE[key] = result.value
        return Result.ok(result.value, cached=False)


def _read_catalog(conn) -> dict:
    tables: dict[str, dict] = {}
    for table, column, col_type, comment, table_comment in conn.execute(
        """
        SELECT c.table_name, c.column_name, c.data_type, c.comment, t.comment
        FROM duckdb_columns() c
        JOIN duckdb_tables() t USING (database_name, schema_name, table_name)
        WHERE c.database_name = current_database() AND c.schema_name = 'main'
        ORDER BY c.table_name, c.column_index
        """
    ).fetchall():
        entry = tables.setdefault(table, {
            "comment": table_comment, "row_estimate": None,
            "columns": [], "references": [], "samples": [],
        })
        entry["columns"].append({
            "name": column, "type": col_type, "comment": comment,
            "pk": False, "fk": None, "values": None, "values_complete": False,
        })

    for name, entry in tables.items():
        t = quote_ident(name)
        entry["row_estimate"] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        entry["samples"] = [tuple(r) for r in conn.execute(f"SELECT * FROM {t} LIMIT 3").fetchall()]
        for col in entry["columns"]:
            if not is_text(col["type"]):
                continue
            c = quote_ident(col["name"])
            # A full DISTINCT is cheap here, so the list is complete, unlike a sample.
            values = conn.execute(
                f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT {MAX_LISTED_VALUES + 1}"
            ).fetchall()
            if len(values) <= MAX_LISTED_VALUES:
                col["values"] = sorted(str(v[0]) for v in values)
                col["values_complete"] = True

    return {"schema": "main", "tables": tables}
