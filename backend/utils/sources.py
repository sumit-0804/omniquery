"""Saved data sources: Postgres connections and uploaded files.

The one place that knows where sources live. The CLI and the API are thin wrappers
over these functions; agents only ever see a source id.
"""

import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
from psycopg2.extensions import parse_dsn

from utils.database import DatabaseUtil, _query_error, clear_schema_cache
from utils.result import Result


def home() -> Path:
    # Read on every call so tests can point it at a temp folder.
    return Path(os.environ.get("OMNIQUERY_HOME") or Path.home() / ".omniquery")


def uploads_dir(source_id: str) -> Path:
    return home() / "uploads" / source_id


# Every extract and transform output is saved here and loaded into one "workspace" source.
WORKSPACE_ID = "workspace"


def outputs_dir() -> Path:
    return home() / "outputs"


def source_dir(source: dict) -> Path:
    return outputs_dir() if source["id"] == WORKSPACE_ID else uploads_dir(source["id"])


def _store() -> Path:
    return home() / "sources.json"


def _load() -> dict[str, dict]:
    try:
        saved = json.loads(_store().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {s["id"]: s for s in saved.get("sources", [])}


def _save(sources: dict[str, dict]) -> None:
    home().mkdir(parents=True, exist_ok=True)
    tmp = _store().with_suffix(".tmp")
    tmp.write_text(json.dumps({"sources": list(sources.values())}, indent=2), encoding="utf-8")
    tmp.replace(_store())


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def list_sources() -> list[dict]:
    return list(_load().values())


def _names(sources) -> str:
    return ", ".join(s["name"] for s in sources) or "none"


def get_source(ref: str) -> Result[dict]:
    if slug(ref or "") == WORKSPACE_ID:
        return Result.ok(workspace())
    sources = _load()
    source = sources.get(slug(ref or ""))
    if source is None:
        return Result.fail("source.not_found", f"No saved source named {ref!r}. Saved: {_names(sources.values())}")
    return Result.ok(source)


def default_source() -> Result[dict]:
    """The only saved source; the workspace when nothing else is saved. Otherwise ask."""
    sources = [s for s in list_sources() if s["id"] != WORKSPACE_ID]
    if len(sources) == 1:
        return Result.ok(sources[0])
    if not sources:
        return Result.ok(workspace())
    return Result.fail("source.ambiguous", f"Several sources are saved; pick one with --source: {_names(sources)}")


def resolve(ref: str | None) -> Result[dict]:
    """The named source, or the only saved one when no name is given."""
    return get_source(ref) if ref else default_source()


def _new_id(name: str) -> Result[str]:
    source_id = slug(name)
    if not source_id:
        return Result.fail("source.bad_name", f"{name!r} has no letters or digits to name a source with.")
    if source_id in _load() or source_id == WORKSPACE_ID:
        return Result.fail("source.exists", f"A source named {name!r} is already saved.")
    return Result.ok(source_id)


def _record(source_id: str, name: str, kind: str, **fields) -> dict:
    return {"id": source_id, "name": name, "kind": kind, **fields, "notes": {},
            "added": datetime.now(UTC).isoformat(timespec="seconds")}


def remove_source(ref: str) -> Result[dict]:
    source = get_source(ref)
    if not source:
        return source
    if source.value["id"] == WORKSPACE_ID:
        # Removing it would delete every saved output, so that is left to the user.
        return Result.fail("source.protected", f"The workspace holds your outputs in {outputs_dir()}; "
                                               "delete files there yourself if you want them gone.")
    sources = _load()
    del sources[source.value["id"]]
    _save(sources)
    if source.value["kind"] == "files":
        shutil.rmtree(source_dir(source.value), ignore_errors=True)
    clear_schema_cache()
    return source


def set_note(ref: str, path: str, text: str) -> Result[dict]:
    """Save a note on "table" or "table.column". Empty text removes it."""
    source = get_source(ref)
    if not source:
        return source
    sources = _load()
    notes = sources[source.value["id"]]["notes"]
    if text.strip():
        notes[path] = text.strip()
    else:
        notes.pop(path, None)
    _save(sources)
    return Result.ok(sources[source.value["id"]])


def masked(url: str) -> str:
    """The URL with its password hidden, for anything printed or sent to the UI."""
    url = re.sub(r"(://[^:/@]*:)[^@]*@", r"\1****@", url)
    return re.sub(r"(password\s*=\s*)\S+", r"\1****", url, flags=re.IGNORECASE)


# ---- Postgres ----

def _pg_config(url: str) -> Result[dict]:
    try:
        config = parse_dsn(url)
    except psycopg2.ProgrammingError as exc:
        return Result.fail("source.bad_url", f"Not a valid Postgres URL: {str(exc).strip()}")
    if "dbname" not in config:
        return Result.fail("source.bad_url", "The URL has no database name, e.g. postgresql://user:pass@host:5432/dbname")
    return Result.ok(config)


# Superuser, CREATE on the schema, or any write privilege on a table in it.
_WRITABLE_SQL = """
SELECT r.rolsuper
    OR has_schema_privilege(%(schema)s, 'CREATE')
    OR EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(schema)s AND c.relkind IN ('r', 'p')
          AND has_table_privilege(c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE'))
FROM pg_roles r WHERE r.rolname = current_user
"""


def check_postgres(url: str, schema: str = "public") -> Result[dict]:
    """Connect once and report {ok, latency_ms, version, tables, readonly}."""
    config = _pg_config(url)
    if not config:
        return config

    started = time.perf_counter()
    try:
        conn = psycopg2.connect(**{"connect_timeout": 10, **config.value})
    except psycopg2.Error as exc:
        return _query_error(exc)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            latency_ms = round((time.perf_counter() - started) * 1000)
            cur.execute("SHOW server_version")
            version = cur.fetchone()[0]
            cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
            if cur.fetchone() is None:
                return Result.fail("source.no_schema", f"The database has no schema named {schema!r}.")
            cur.execute(
                """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')""",
                (schema,),
            )
            tables = cur.fetchone()[0]
            cur.execute(_WRITABLE_SQL, {"schema": schema})
            writable = bool(cur.fetchone()[0])
    except psycopg2.Error as exc:
        return _query_error(exc)
    finally:
        conn.close()

    return Result.ok({"ok": True, "latency_ms": latency_ms, "version": version,
                      "tables": tables, "readonly": not writable})


def readonly_role_sql(schema: str) -> str:
    return (
        "CREATE ROLE omniquery_ro LOGIN PASSWORD '...';\n"
        f"GRANT USAGE ON SCHEMA {schema} TO omniquery_ro;\n"
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO omniquery_ro;"
    )


def add_postgres(name: str, url: str, schema: str = "public") -> Result[dict]:
    source_id = _new_id(name)
    if not source_id:
        return source_id
    tested = check_postgres(url, schema)
    if not tested:
        return tested

    # Build the catalog now, so a schema the agent cannot read fails here and not on the first question.
    catalog = DatabaseUtil(_pg_config(url).value, schema=schema).schema_catalog()
    if not catalog:
        return catalog

    record = _record(source_id.value, name, "postgres", url=url, schema=schema,
                     readonly=tested.value["readonly"])
    sources = _load()
    sources[record["id"]] = record
    _save(sources)
    return Result.ok(record, **tested.value)


# ---- Files ----

def add_files(name: str, paths: list[str]) -> Result[dict]:
    from utils.duck import SUPPORTED, build_database

    source_id = _new_id(name)
    if not source_id:
        return source_id
    files = [Path(p) for p in paths]
    for f in files:
        if not f.is_file():
            return Result.fail("source.bad_file", f"{f} is not a file.")
        if f.suffix.lower() not in SUPPORTED:
            return Result.fail("source.bad_file", f"{f.name}: only {', '.join(sorted(SUPPORTED))} files are supported.")

    folder = uploads_dir(source_id.value)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    copies = []
    for f in files:
        target = folder / f.name
        n = 2
        while target.exists():
            target = folder / f"{f.stem}_{n}{f.suffix}"
            n += 1
        shutil.copy2(f, target)
        copies.append(target)

    built = build_database(folder / "data.duckdb", copies)
    if not built:
        shutil.rmtree(folder, ignore_errors=True)
        return built

    record = _record(source_id.value, name, "files", files=[c.name for c in copies],
                     tables=built.value, readonly=True)
    sources = _load()
    sources[record["id"]] = record
    _save(sources)
    return Result.ok(record)


# ---- Workspace ----

def workspace() -> dict:
    """The workspace source, created (empty) on first use."""
    import duckdb

    sources = _load()
    if WORKSPACE_ID not in sources:
        outputs_dir().mkdir(parents=True, exist_ok=True)
        duckdb.connect(str(outputs_dir() / "data.duckdb")).close()
        sources[WORKSPACE_ID] = _record(WORKSPACE_ID, "workspace", "files", files=[], tables=[], readonly=True)
        _save(sources)
    return sources[WORKSPACE_ID]


def output_path(name: str, fmt: str) -> Path:
    """A new file in outputs/ for `name`, never an existing one."""
    from utils.duck import table_name

    outputs_dir().mkdir(parents=True, exist_ok=True)
    used = {f.stem for f in outputs_dir().iterdir()}
    return outputs_dir() / f"{table_name(name, used)}.{fmt}"


def add_output(file: Path) -> Result[str]:
    """Load an output file into the workspace as a table; returns the table name."""
    from utils.duck import add_table

    workspace()
    table = add_table(outputs_dir() / "data.duckdb", file, file.stem)
    if not table:
        return table
    sources = _load()
    sources[WORKSPACE_ID]["files"].append(file.name)
    sources[WORKSPACE_ID]["tables"].append(table.value)
    _save(sources)
    return table


def list_outputs() -> list[dict]:
    from utils.duck import SUPPORTED

    record = workspace()
    table_of = dict(zip(record["files"], record["tables"], strict=True))
    return [
        {"name": f.name, "bytes": f.stat().st_size, "table": table_of.get(f.name)}
        for f in sorted(outputs_dir().iterdir(), key=lambda f: f.stat().st_mtime)
        if f.suffix.lower() in SUPPORTED
    ]


# ---- Opening ----

def refresh_source(ref: str) -> Result[dict]:
    """Drop cached catalogs and read the source's tables again."""
    from utils.duck import clear_catalog_cache

    clear_schema_cache()
    clear_catalog_cache()
    db = open_source(ref)
    return db.value.schema_catalog() if db else db


def open_source(ref: str) -> Result:
    """A connector for the source: dialect, schema_catalog(), explain_sql(), execute_sql().

    meta["notes"] carries the source's notes for the prompt.
    """
    source = get_source(ref)
    if not source:
        return source
    s = source.value
    if s["kind"] == "files":
        from utils.duck import DuckDBUtil

        return Result.ok(DuckDBUtil(source_dir(s) / "data.duckdb"), notes=s["notes"], source=s)
    config = _pg_config(s["url"])
    if not config:
        return config
    return Result.ok(DatabaseUtil(config.value, schema=s["schema"]), notes=s["notes"], source=s)
