"""The HTTP API the web UI talks to. Thin wrappers over utils/sources.py and the data agent.

Endpoints are plain `def`: the agent code blocks, so FastAPI runs them in its thread pool.
"""

import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from pydantic import BaseModel, ConfigDict, Field

from agents.chart_analyst import json_value
from agents.data_agent import data_agent
from api.events import run_events
from utils import sources
from utils.config import PROJECT_ROOT, REPO_URL, demo_mode
from utils.llm_pick import usage_report
from utils.result import Result

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
FRONTEND_DIST = PROJECT_ROOT.parent / "frontend" / "dist"

# The hosted demo spends the owner's free model budget, so it is shared out.
DEMO_PER_VISITOR = parse("10/hour")
DEMO_PER_DAY = parse("300/day")
DEMO_SOURCE = "rides"

_log = logging.getLogger(__name__)

_STATUS = {"source.not_found": 404, "api.not_found": 404, "source.protected": 400, "db.unreachable": 502,
           "upload.too_large": 413}


class _DemoDisabled(Exception):
    pass


def _not_in_demo() -> None:
    if demo_mode():
        raise _DemoDisabled()


def _error(result: Result) -> JSONResponse:
    return JSONResponse({"code": result.code, "detail": result.error}, status_code=_STATUS.get(result.code, 400))


def _public(source: dict) -> dict:
    """A source as the UI may see it: never the raw connection URL, and in the demo not even the host."""
    shown = {k: v for k, v in source.items() if k != "url"}
    if "url" in source:
        shown["url"] = "hosted demo database" if demo_mode() else sources.masked(source["url"])
    if source.get("kind") == "postgres" and not source.get("readonly"):
        shown["readonly_role_sql"] = sources.readonly_role_sql(source.get("schema", "public"))
    return shown


def _sse(events: Iterator[dict]) -> StreamingResponse:
    body = (f"data: {json.dumps(e, default=str)}\n\n" for e in events)
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _failed_run(code: str, detail: str, headline: str = "") -> Iterator[dict]:
    # Streams can't return a JSON error body that EventSource could read, so fail in-band.
    yield {"type": "error", "t": 0.0, "code": code, "detail": detail, "attempts": 0,
           **({"headline": headline} if headline else {})}
    yield {"type": "done", "t": 0.0, "elapsed": 0.0, "outcome": "error"}


def _add_demo_source() -> None:
    """The demo's disk is wiped on every restart, so its one source is saved again at startup."""
    url = os.environ.get("DEMO_DATABASE_URL")
    if not url:
        _log.error("OMNIQUERY_DEMO is set but DEMO_DATABASE_URL is not; the demo has no data.")
        return
    if sources.get_source(DEMO_SOURCE):
        return
    added = sources.add_postgres(DEMO_SOURCE, url)
    if not added:
        _log.error("Could not add the demo source: %s %s", added.code, added.error)


def _start(question: str, source: str | None) -> tuple[str, Iterator[dict]]:
    resolved = sources.resolve(source)
    if not resolved:
        return "", _failed_run(resolved.code, resolved.error)
    thread_id = uuid.uuid4().hex
    payload = {"messages": [HumanMessage(content=question)], "source_id": resolved.value["id"]}
    return thread_id, run_events(payload, thread_id)


def _resume(thread_id: str, reply: str) -> Iterator[dict]:
    paused = data_agent.get_state({"configurable": {"thread_id": thread_id}})
    if not paused.next:
        return _failed_run("run.not_paused", "That run is not waiting for an answer; ask the question again.")
    return run_events(Command(resume=reply), thread_id, new_question=False)


class ChatRequest(BaseModel):
    q: str = ""
    source: str | None = None
    thread_id: str | None = None
    reply: str | None = None


class PostgresRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dsn: str
    # Sent as "schema", which would shadow a pydantic method name.
    schema_name: str = Field("public", alias="schema")
    name: str = ""


class NoteRequest(BaseModel):
    path: str
    note: str


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if demo_mode():
            _add_demo_source()
        yield

    app = FastAPI(title="OmniQuery", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    limiter = MovingWindowRateLimiter(MemoryStorage())
    blocked = [Depends(_not_in_demo)]

    @app.exception_handler(_DemoDisabled)
    def demo_disabled(_request: Request, _exc: _DemoDisabled):
        return JSONResponse({"code": "demo.disabled",
                             "detail": f"This is turned off in the hosted demo. Run OmniQuery locally to use it: {REPO_URL}"},
                            status_code=403)

    def over_limit(request: Request) -> Iterator[dict] | None:
        if not demo_mode():
            return None
        # Behind Render's proxy this is the forwarded visitor address (see `serve`).
        visitor = request.client.host if request.client else "unknown"
        if not limiter.hit(DEMO_PER_VISITOR, "visitor", visitor):
            return _failed_run("demo.rate_limited", "10 questions per hour per visitor.",
                               "You've asked 10 questions this hour, the demo's limit. Try again later, "
                               "or run OmniQuery locally with your own key.")
        if not limiter.hit(DEMO_PER_DAY, "everyone"):
            return _failed_run("demo.rate_limited", "300 questions per day in total.",
                               "The demo's questions for today are used up. Try again tomorrow, "
                               "or run OmniQuery locally with your own key.")
        return None

    @app.get("/api/config")
    def config():
        return {"demo": demo_mode(), "repo": REPO_URL}

    @app.get("/api/ask")
    def ask(request: Request, q: str, source: str | None = None):
        return _sse(over_limit(request) or _start(q, source)[1])

    @app.get("/api/ask/{thread_id}/resume")
    def resume(request: Request, thread_id: str, reply: str = ""):
        return _sse(over_limit(request) or _resume(thread_id, reply))

    @app.post("/api/chat", dependencies=blocked)
    def chat(body: ChatRequest):
        """The same run as /api/ask, collected into one response, for scripts and tests."""
        if body.thread_id:
            thread_id, events = body.thread_id, _resume(body.thread_id, body.reply or "")
        else:
            thread_id, events = _start(body.q, body.source)
        collected = list(events)
        answer = "".join(e["text"] for e in collected if e["type"] == "answer")
        outcome = next((e["outcome"] for e in reversed(collected) if e["type"] == "done"), "error")
        return {"thread_id": thread_id, "outcome": outcome, "answer": answer, "events": collected}

    @app.get("/api/sources")
    def list_sources():
        return [_public(s) for s in sources.list_sources()]

    @app.post("/api/sources/test", dependencies=blocked)
    def test_source(body: PostgresRequest):
        result = sources.check_postgres(body.dsn, body.schema_name)
        return result.value if result else _error(result)

    @app.post("/api/sources/postgres", dependencies=blocked)
    def add_postgres(body: PostgresRequest):
        result = sources.add_postgres(body.name, body.dsn, body.schema_name)
        if not result:
            return _error(result)
        response = {"source": _public(result.value), **result.meta}
        if not result.meta["readonly"]:
            response["readonly_role_sql"] = sources.readonly_role_sql(body.schema_name)
        return response

    @app.post("/api/sources/files", dependencies=blocked)
    def add_files(name: Annotated[str, Form()], files: Annotated[list[UploadFile], File()]):
        folder = Path(tempfile.mkdtemp(prefix="omniquery-upload-"))
        try:
            paths, total = [], 0
            for upload in files:
                # Only the base name: an upload cannot choose where it lands.
                target = folder / Path(upload.filename or "upload").name
                with target.open("wb") as out:
                    while chunk := upload.file.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            return _error(Result.fail("upload.too_large", "Uploads are limited to 200 MB."))
                        out.write(chunk)
                paths.append(str(target))
            result = sources.add_files(name, paths)
            return {"source": _public(result.value)} if result else _error(result)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @app.delete("/api/sources/{source_id}", dependencies=blocked)
    def remove_source(source_id: str):
        result = sources.remove_source(source_id)
        return {"removed": result.value["id"]} if result else _error(result)

    @app.get("/api/sources/{source_id}/catalog")
    def catalog(source_id: str):
        db = sources.open_source(source_id)
        if not db:
            return _error(db)
        result = db.value.schema_catalog()
        if not result:
            return _error(result)
        return _catalog_json(source_id, result.value, db.meta.get("notes") or {})

    @app.put("/api/sources/{source_id}/notes", dependencies=blocked)
    def set_note(source_id: str, body: NoteRequest):
        # Notes are applied when the prompt is built, so the next question sees them.
        result = sources.set_note(source_id, body.path, body.note)
        return {"notes": result.value["notes"]} if result else _error(result)

    @app.post("/api/sources/{source_id}/refresh", dependencies=blocked)
    def refresh(source_id: str):
        result = sources.refresh_source(source_id)
        return {"tables": len(result.value["tables"])} if result else _error(result)

    @app.get("/api/providers")
    def providers():
        return usage_report()

    @app.get("/api/outputs")
    def outputs():
        return sources.list_outputs()

    @app.get("/api/outputs/{name}", dependencies=blocked)
    def output_file(name: str):
        # Only names the outputs listing returns, so a crafted name cannot reach other files.
        if name not in {o["name"] for o in sources.list_outputs()}:
            return _error(Result.fail("source.not_found", f"No output named {name!r}."))
        return FileResponse(sources.outputs_dir() / name, filename=name)

    @app.get("/api/health")
    def health():
        return {"ok": True, "sources": len(sources.list_sources()), "providers": len(usage_report())}

    if FRONTEND_DIST.is_dir():
        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            # The built React app: real files as they are, every other path gets index.html.
            if path.startswith("api/"):
                return _error(Result.fail("api.not_found", f"No API route {path!r}."))
            candidate = (FRONTEND_DIST / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


def _catalog_json(source_id: str, catalog: dict, notes: dict) -> dict:
    tables = []
    for name, t in catalog["tables"].items():
        tables.append({
            "name": name, "comment": t["comment"], "row_estimate": t["row_estimate"], "note": notes.get(name, ""),
            "references": t["references"],
            "columns": [c | {"note": notes.get(f"{name}.{c['name']}", "")} for c in t["columns"]],
            "samples": [[json_value(v) for v in row] for row in t["samples"]],
        })
    return {"source": source_id, "schema": catalog["schema"], "tables": tables}


app = create_app()
