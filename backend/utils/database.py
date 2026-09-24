from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import cursor as PgCursor

from utils.result import Result

_SCHEMA_CACHE: dict[tuple, dict] = {}

# Hard cap on rows pulled from a cursor, whatever LIMIT the SQL has (or lacks).
MAX_FETCH_ROWS = 1000

# Exports (ETL) read every row, in chunks, with a longer timeout and a sanity cap.
EXPORT_TIMEOUT_MS = 120_000
EXPORT_CHUNK_ROWS = 10_000
EXPORT_MAX_ROWS = 5_000_000

# Postgres type OIDs -> DuckDB column types, so every chunk lands in the same types.
_DUCKDB_TYPES = {
    16: "BOOLEAN", 20: "BIGINT", 21: "INTEGER", 23: "INTEGER",
    700: "DOUBLE", 701: "DOUBLE", 1700: "DOUBLE",
    1082: "DATE", 1114: "TIMESTAMP", 1184: "TIMESTAMPTZ",
}


def format_rows(columns: list[str], rows: list, limit: int) -> str:
    """Rows as text for a prompt, capped at `limit` so a big result cannot flood the context."""
    if not columns:
        return ""
    lines = [" | ".join(columns)]
    lines.extend(" | ".join("" if v is None else str(v) for v in row) for row in rows[:limit])
    if len(rows) > limit:
        lines.append(f"...and {len(rows) - limit} more rows")
    return "\n".join(lines)


def _query_error(exc: psycopg2.Error) -> Result:
    """One mapping for every query path. The `{pgcode}: {message}` shape is relied on by retries."""
    if isinstance(exc, psycopg2.OperationalError):
        return Result.fail("db.unreachable", str(exc).strip())
    if isinstance(exc, psycopg2.errors.ReadOnlySqlTransaction):
        return Result.fail("db.write_rejected", str(exc).strip())
    return Result.fail("db.query_failed", f"{exc.pgcode or '?'}: {str(exc).strip()}")


class DatabaseUtil:
    dialect = "postgres"

    def __init__(self, config: dict, schema: str = "public", statement_timeout_ms: int = 15_000):
        # No I/O here: a connection failure must be a Result at the call site,
        # not a half-built object whose failure surfaces much later.
        self.config = config
        self.schema = schema
        self.statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _cursor(self, readonly: bool = True) -> Iterator[PgCursor]:
        conn = psycopg2.connect(**{"connect_timeout": 10, **self.config})
        try:
            # Deterministic backstop: Postgres refuses writes regardless of what
            # the safety check decided. Must be set before any transaction.
            conn.set_session(readonly=readonly, autocommit=False)
            # `with conn` manages the transaction, not the connection -- closing
            # is the outer finally's job.
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SET LOCAL statement_timeout = %s", (str(self.statement_timeout_ms),)
                    )
                    cur.execute(
                        sql.SQL("SET LOCAL search_path = {}").format(sql.Identifier(self.schema))
                    )
                    yield cur
        finally:
            conn.close()

    def execute_sql(self, query: str) -> Result[dict]:
        try:
            with self._cursor(readonly=True) as cur:
                cur.execute(query)
                columns = [d[0] for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(MAX_FETCH_ROWS + 1) if cur.description else []
        except psycopg2.Error as exc:
            return _query_error(exc)

        rows = [list(row) for row in fetched[:MAX_FETCH_ROWS]]
        return Result.ok({
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(fetched) > MAX_FETCH_ROWS,
        })

    def export(self, query: str, target, fmt: str) -> Result[dict]:
        """Stream every row of `query` into a file, through a server-side cursor."""
        import duckdb
        import pandas as pd

        from utils.duck import COPY_OPTIONS, quote_ident, quote_literal

        query = query.strip().rstrip(";")
        out = duckdb.connect()
        rows = 0
        try:
            with self._cursor(readonly=True) as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (str(EXPORT_TIMEOUT_MS),))
                with cur.connection.cursor(name="omniquery_export") as stream:
                    stream.itersize = EXPORT_CHUNK_ROWS
                    stream.execute(query)
                    while chunk := stream.fetchmany(EXPORT_CHUNK_ROWS):
                        if rows == 0:
                            columns = [d[0] for d in stream.description]
                            types = [_DUCKDB_TYPES.get(d[1], "VARCHAR") for d in stream.description]
                            out.execute("CREATE TABLE export ({})".format(
                                ", ".join(f"{quote_ident(c)} {t}" for c, t in zip(columns, types, strict=True))))
                        rows += len(chunk)
                        if rows > EXPORT_MAX_ROWS:
                            return Result.fail("etl.too_large", f"More than {EXPORT_MAX_ROWS:,} rows; narrow the query.")
                        frame = pd.DataFrame(chunk, columns=[f"c{i}" for i in range(len(columns))], dtype=object)
                        out.register("chunk", frame)
                        out.execute("INSERT INTO export SELECT * FROM chunk")
                        out.unregister("chunk")
            if rows == 0:
                return Result.fail("etl.empty", "The query returned no rows, so nothing was saved.")
            out.execute(f"COPY export TO {quote_literal(target.as_posix())} {COPY_OPTIONS[fmt]}")
        except psycopg2.Error as exc:
            return _query_error(exc)
        finally:
            out.close()
        return Result.ok({"path": str(target), "rows": rows, "columns": columns})

    def explain_sql(self, query: str) -> Result[None]:
        """Plan the query without running it. Catches wrong tables and columns in milliseconds."""
        try:
            with self._cursor(readonly=True) as cur:
                cur.execute(f"EXPLAIN {query}")
        except psycopg2.Error as exc:
            return _query_error(exc)
        return Result.ok(None)

    def schema_catalog(self) -> Result[dict]:
        """Tables, columns, keys, comments, row estimates and sample values for the schema.

        Cached per database and schema. User notes are not part of it; they are added
        at render time, so a new note needs no cache clearing.
        """
        schema_name = self.schema
        key = (self.config.get("host"), self.config.get("port"), self.config.get("dbname"), schema_name)
        if key in _SCHEMA_CACHE:
            return Result.ok(_SCHEMA_CACHE[key], cached=True)

        try:
            with self._cursor(readonly=True) as cur:
                tables = _read_columns(cur, schema_name)
                _read_keys(cur, schema_name, tables)
                stats = _read_value_stats(cur, schema_name)
                for name, table in tables.items():
                    _read_samples(cur, schema_name, name, table, stats)
        except psycopg2.OperationalError as exc:
            return Result.fail("db.unreachable", str(exc).strip())
        except psycopg2.Error as exc:
            return Result.fail("db.schema_failed", f"{exc.pgcode or '?'}: {str(exc).strip()}")

        catalog = {"schema": schema_name, "tables": tables}
        # Cache successes only; caching an error is how a traceback ends up in a prompt.
        _SCHEMA_CACHE[key] = catalog
        return Result.ok(catalog, cached=False)


def clear_schema_cache() -> None:
    _SCHEMA_CACHE.clear()


# Text columns with this many distinct values or fewer have them listed, so the
# model writes 'cancelled' rather than guessing 'canceled'.
MAX_LISTED_VALUES = 20
_VALUE_SAMPLE_ROWS = 10_000


def _read_columns(cur, schema_name: str) -> dict:
    # pg_catalog rather than information_schema: exact types such as numeric(10,2),
    # plus comments and row estimates, in one query.
    cur.execute(
        """
        SELECT cls.relname, att.attname, format_type(att.atttypid, att.atttypmod),
               col_description(cls.oid, att.attnum), obj_description(cls.oid, 'pg_class'),
               cls.reltuples::bigint
        FROM pg_class cls
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
        WHERE ns.nspname = %s AND cls.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY cls.relname, att.attnum
        """,
        (schema_name,),
    )
    tables: dict[str, dict] = {}
    for table, column, col_type, col_comment, table_comment, estimate in cur.fetchall():
        entry = tables.setdefault(table, {
            "comment": table_comment,
            # reltuples is -1 (or 0) until the table has been analysed.
            "row_estimate": estimate if estimate and estimate > 0 else None,
            "columns": [],
            "references": [],
            "samples": [],
        })
        entry["columns"].append({
            "name": column, "type": col_type, "comment": col_comment,
            "pk": False, "fk": None, "values": None, "values_complete": False,
        })
    return tables


def _read_keys(cur, schema_name: str, tables: dict) -> None:
    cur.execute(
        """
        SELECT con.contype, cls.relname, att.attname, fcls.relname, fatt.attname
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = k.attnum
        LEFT JOIN pg_class fcls ON fcls.oid = con.confrelid
        LEFT JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = k.ord
        LEFT JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = fk.attnum
        WHERE ns.nspname = %s AND con.contype IN ('p', 'f')
        """,
        (schema_name,),
    )
    for kind, table, column, ref_table, ref_column in cur.fetchall():
        if table not in tables:
            continue
        col = next((c for c in tables[table]["columns"] if c["name"] == column), None)
        if col is None:
            continue
        if kind == "p":
            col["pk"] = True
        else:
            col["fk"] = f"{ref_table}.{ref_column}"
            if ref_table not in tables[table]["references"]:
                tables[table]["references"].append(ref_table)


def is_text(col_type: str) -> bool:
    return col_type.lower().startswith(("character", "text", "varchar", "citext"))


def _read_value_stats(cur, schema_name: str) -> dict[tuple[str, str], list[str]]:
    """Complete value lists for low-cardinality columns, from the planner statistics.

    ANALYZE samples randomly across the whole table, so unlike reading the first N
    rows it cannot miss values that happen to be stored late. Only lists that are
    provably complete (every distinct value is a most-common value) are returned.
    """
    cur.execute(
        """
        SELECT tablename, attname, n_distinct, most_common_vals::text::text[]
        FROM pg_stats
        WHERE schemaname = %s AND n_distinct > 0 AND n_distinct <= %s
        """,
        (schema_name, MAX_LISTED_VALUES),
    )
    return {
        (table, column): sorted(values)
        for table, column, n_distinct, values in cur.fetchall()
        if values and len(values) == int(n_distinct)
    }


def _read_samples(cur, schema_name: str, table: str, entry: dict, stats: dict) -> None:
    relation = sql.Identifier(schema_name, table)
    cur.execute(sql.SQL("SELECT * FROM {} LIMIT 3").format(relation))
    entry["samples"] = [tuple(row) for row in cur.fetchall()]

    for col in entry["columns"]:
        if not is_text(col["type"]) or col["pk"]:
            continue
        if (table, col["name"]) in stats:
            col["values"] = stats[(table, col["name"])]
            col["values_complete"] = True
            continue
        # No usable statistics: a bounded sample, which can miss rare or late-stored
        # values, so it is shown as a sample rather than as the full list.
        column = sql.Identifier(col["name"])
        cur.execute(
            sql.SQL(
                "SELECT DISTINCT {c} FROM (SELECT {c} FROM {t} LIMIT {n}) s WHERE {c} IS NOT NULL LIMIT {m}"
            ).format(
                c=column, t=relation,
                n=sql.Literal(_VALUE_SAMPLE_ROWS), m=sql.Literal(MAX_LISTED_VALUES + 1),
            )
        )
        values = sorted(str(v[0]) for v in cur.fetchall())
        if len(values) <= MAX_LISTED_VALUES:
            col["values"] = values


def notes_in(catalog: dict, tables: list[str] | None, notes: dict | None) -> list[str]:
    """Note paths ("table" or "table.column") that render_schema actually includes."""
    used = []
    for name in _shown(catalog, tables):
        columns = {c["name"] for c in catalog["tables"][name]["columns"]}
        for path in notes or {}:
            table, _, column = path.partition(".")
            if table == name and (not column or column in columns):
                used.append(path)
    return sorted(used)


def _shown(catalog: dict, tables: list[str] | None) -> list[str]:
    return [t for t in catalog["tables"] if tables is None or t in tables]


def render_schema(catalog: dict, tables: list[str] | None = None, notes: dict | None = None) -> str:
    """The catalog as prompt text, limited to `tables` when given, with the user's notes."""
    notes = notes or {}
    sections = [f"Schema: {catalog['schema']}"]

    for name in _shown(catalog, tables):
        t = catalog["tables"][name]
        size = f" (~{t['row_estimate']:,} rows)" if t["row_estimate"] else ""
        comment = f"  -- {t['comment']}" if t["comment"] else ""
        note = f"  note: {notes[name]}" if notes.get(name) else ""
        lines = [f"\nTable: {name}{size}{comment}{note}"]

        for c in t["columns"]:
            parts = [f"  {c['name']} {c['type']}"]
            if c["pk"]:
                parts.append("PK")
            if c["fk"]:
                parts.append(f"-> {c['fk']}")
            if c["values"]:
                label = "values" if c["values_complete"] else "sample values (may be incomplete)"
                parts.append(f" {label}: {', '.join(c['values'])}")
            if c["comment"]:
                parts.append(f" -- {c['comment']}")
            column_note = notes.get(f"{name}.{c['name']}")
            if column_note:
                parts.append(f" note: {column_note}")
            lines.append(" ".join(parts))

        if t["samples"]:
            # Plain values, not Python reprs like Decimal('12.50'): easier to read, fewer tokens.
            lines.append("  Sample rows:")
            lines.extend("    " + " | ".join("" if v is None else str(v) for v in row) for row in t["samples"])
        sections.append("\n".join(lines))

    return "\n".join(sections)
