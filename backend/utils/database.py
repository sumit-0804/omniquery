from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import cursor as PgCursor

from utils.config import db_config
from utils.result import Result

_SCHEMA_CACHE: dict[tuple, str] = {}


def _format_rows(columns: list[str], rows: list[tuple]) -> str:
    if not columns:
        return ""
    lines = [" | ".join(columns)]
    lines.extend(" | ".join("" if v is None else str(v) for v in row) for row in rows)
    return "\n".join(lines)


class DatabaseUtil:

    def __init__(self, config: dict | None = None, statement_timeout_ms: int = 15_000):
        # No I/O here: a connection failure must be a Result at the call site,
        # not a half-built object whose failure surfaces much later.
        self.config = config or db_config()
        self.statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _cursor(self, readonly: bool = True) -> Iterator[PgCursor]:
        conn = psycopg2.connect(**self.config)
        try:
            # Deterministic backstop: Postgres refuses writes regardless of what
            # the LLM safety judge decided. Must be set before any transaction.
            conn.set_session(readonly=readonly, autocommit=False)
            # `with conn` manages the transaction, not the connection -- closing
            # is the outer finally's job.
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SET LOCAL statement_timeout = %s", (str(self.statement_timeout_ms),)
                    )
                    yield cur
        finally:
            conn.close()

    def execute_sql(self, query: str) -> Result[str]:
        try:
            with self._cursor(readonly=True) as cur:
                cur.execute(query)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
        except psycopg2.OperationalError as exc:
            return Result.fail("db.unreachable", str(exc).strip())
        except psycopg2.errors.ReadOnlySqlTransaction as exc:
            return Result.fail("db.write_rejected", str(exc).strip())
        except psycopg2.Error as exc:
            return Result.fail("db.query_failed", f"{exc.pgcode or '?'}: {str(exc).strip()}")

        return Result.ok(_format_rows(columns, rows), row_count=len(rows), columns=columns)

    def schema_details(self, schema_name: str = "public") -> Result[str]:
        key = (self.config["host"], self.config["dbname"], schema_name)
        if key in _SCHEMA_CACHE:
            return Result.ok(_SCHEMA_CACHE[key], cached=True)

        try:
            with self._cursor(readonly=True) as cur:
                # One query for every column, rather than one per table.
                cur.execute(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    ORDER BY table_name, ordinal_position;
                    """,
                    (schema_name,),
                )
                by_table: dict[str, list[str]] = {}
                for table_name, column_name, data_type in cur.fetchall():
                    by_table.setdefault(table_name, []).append(f"  {column_name} ({data_type})")

                sections = [f"Database Schema: {schema_name}"]
                for table_name, columns in by_table.items():
                    cur.execute(
                        sql.SQL("SELECT * FROM {} LIMIT 3").format(
                            sql.Identifier(schema_name, table_name)
                        )
                    )
                    samples = [str(row) for row in cur.fetchall()]
                    sections.append(
                        f"\nTable: {table_name}\n"
                        + "\n".join(columns)
                        + "\n  Sample rows:\n"
                        + "\n".join(f"    {s}" for s in samples)
                    )
                rendered = "\n".join(sections)
        except psycopg2.OperationalError as exc:
            return Result.fail("db.unreachable", str(exc).strip())
        except psycopg2.Error as exc:
            return Result.fail("db.schema_failed", f"{exc.pgcode or '?'}: {str(exc).strip()}")

        # Cache successes only; caching an error is how a traceback ends up in a prompt.
        _SCHEMA_CACHE[key] = rendered
        return Result.ok(rendered, cached=False)


def clear_schema_cache() -> None:
    _SCHEMA_CACHE.clear()
