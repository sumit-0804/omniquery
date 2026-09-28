"""Turn a data_agent run into the events the web UI draws (see BACKEND_CONTRACT.md).

Steps are simply the graph nodes that ran, in run order, so adding or renaming a node
needs no change here beyond, optionally, a friendlier label.
"""

import logging
import time
from collections.abc import Iterator

from langchain_core.messages import AIMessageChunk

from agents.chart_analyst import json_value
from agents.data_agent import data_agent
from agents.etl_analyst import MAX_ETL_ATTEMPTS
from agents.registry import REGISTRY
from agents.sql_analyst import MAX_SQL_ATTEMPTS, ROW_LIMIT
from utils.llm_pick import CHAINS, ModelCoolingDown, NoProviderConfigured, first_model, is_transient, text_of
from utils.ratelimit import QuotaExceeded

_log = logging.getLogger(__name__)

LABELS = {
    "router_node": "Choosing an agent",
    "ask_human": "Asking you",
    "prompt_query_context": "Selecting tables",
    "generate_sql": "Generating SQL",
    "check_readonly": "Checking the query is read-only",
    "validate_sql": "Validating the query",
    "execute_sql": "Executing",
    "represent_final_answer": "Composing the answer",
    "canceled_sql": "Not run",
    "report_failure": "Reporting the failure",
    "design_chart": "Designing the chart",
    "read_context": "Reading the source",
    "plan_etl": "Planning the job",
    "run_etl": "Running",
    "write_output": "Writing the output",
}

# Only these nodes' tokens are the user's answer; others stream SQL, chart JSON or plans.
_ANSWER_NODES = {"represent_final_answer"}
_AGENT_NODES = {spec.node_name for spec in REGISTRY.values()}
_RETRYABLE = {"db.query_failed", "etl.bad_plan"}
_PROVIDER_OF_MODEL = {model: provider for chain in CHAINS.values() for provider, model, _ in chain}


def label(node: str) -> str:
    return LABELS.get(node) or node.replace("_", " ").capitalize()


def run_events(payload, thread_id: str, new_question: bool = True) -> Iterator[dict]:
    """Stream one run (a new question, or a Command resuming a paused one) as events."""
    run = _Run(thread_id)
    if new_question:
        yield run.event("step", id="submitted", label="Question submitted", status="done")
    # The first node is known before it reports, so the rail shows the wait instead of silence.
    yield from run._activate("router_node" if new_question else "ask_human")
    try:
        config = {"configurable": {"thread_id": thread_id}}
        for _ns, mode, chunk in data_agent.stream(
            payload, config, stream_mode=["updates", "messages"], subgraphs=True,
        ):
            if mode == "messages":
                yield from run.on_token(*chunk)
            else:
                for node, update in chunk.items():
                    yield from run.on_update(node, update)
        yield from run.finish()
    except Exception as exc:
        # Details go to the server log; the client gets a stable code, not internals.
        _log.exception("run %s failed", thread_id)
        yield run.event("error", attempts=run.attempts, **_failure(exc))
        yield run.event("done", elapsed=run.elapsed(), outcome="error")


def _failure(exc: Exception) -> dict:
    if isinstance(exc, NoProviderConfigured):
        return {"code": "llm.unavailable", "detail": str(exc), "headline": str(exc)}
    # Every model in the chain was busy, cooling down or out of today's budget.
    if isinstance(exc, (QuotaExceeded, ModelCoolingDown)) or is_transient(exc):
        return {"code": "llm.unavailable", "detail": f"{type(exc).__name__}: {exc}"[:300],
                "headline": "Every model is busy or out of today's free budget. Try again later."}
    return {"code": "server.error", "detail": type(exc).__name__}


class _Run:
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.started = time.perf_counter()
        self.active: set[str] = set()
        self.answered = False
        self.outcome = "answer"
        self.model = ""
        self.error = ("", "")
        self.attempts = 0
        self.notes: list[str] = []

    def elapsed(self) -> float:
        return round(time.perf_counter() - self.started, 3)

    def event(self, kind: str, **fields) -> dict:
        return {"type": kind, "t": self.elapsed(), **fields}

    def _activate(self, node: str) -> Iterator[dict]:
        if node and node not in self.active:
            self.active.add(node)
            yield self.event("step", id=node, label=label(node), status="active")

    def on_token(self, message, meta: dict) -> Iterator[dict]:
        # Whole messages also arrive once a node finishes; only the streamed pieces matter here.
        if not isinstance(message, AIMessageChunk):
            return
        node = meta.get("langgraph_node", "")
        yield from self._activate(node)
        if meta.get("ls_model_name"):
            self.model = meta["ls_model_name"]
        text = text_of(message, strip=False)
        if node in _ANSWER_NODES and text:
            self.answered = True
            yield self.event("answer", text=text, done=False)

    def on_update(self, node: str, update) -> Iterator[dict]:
        if node == "__interrupt__":
            self.outcome = "clarify"
            yield self.event("clarify", thread_id=self.thread_id, **update[0].value)
            return
        update = update or {}
        if node in _AGENT_NODES:
            yield from self._agent_result(update)
            return

        if update.get("error_code"):
            self.error = (update["error_code"], update.get("error_detail", ""))
        failed = bool(update.get("error_code")) or (node == "check_readonly" and update.get("is_safe") == "No")
        yield from self._activate(node)
        self.active.discard(node)  # a retry runs the node again and shows it again
        yield self.event("step", id=node, label=label(node), status="failed" if failed else "done",
                         **({"detail": d} if (d := self._detail(node, update)) else {}))
        yield from self._payloads(node, update)

    def _detail(self, node: str, update: dict) -> str:
        if node == "router_node" and update.get("route_response"):
            return f"{update['route_response']}, confidence {update.get('route_confidence', 0):.2f}"
        if node == "prompt_query_context" and update.get("selected_tables"):
            notes = len(update.get("notes_used") or [])
            return ", ".join(update["selected_tables"]) + (f" + {notes} notes" if notes else "")
        return ""

    def _payloads(self, node: str, u: dict) -> Iterator[dict]:
        if node in ("router_node", "ask_human") and u.get("route_response"):
            yield self.event("routing", agent=u["route_response"],
                             confidence=u.get("route_confidence", 0.0), source=u.get("route_source", ""))
        if node == "ask_human" and u.get("messages"):
            # The user cancelled, or asked too many questions: the run ends with this message.
            self.outcome, self.answered = "stopped", True
            yield self.event("answer", text=text_of(u["messages"][-1]), done=False)
        if node == "prompt_query_context":
            self.notes = u.get("notes_used") or []
        if node == "generate_sql":
            yield self.event("sql", query=u.get("generated_sql_query", ""), attempt=u.get("sql_attempts", 1),
                             notes=self.notes)
        if node == "check_readonly":
            yield self.event("judge", safe=u.get("is_safe") == "Yes", comments=u.get("comments", ""))
            if u.get("is_safe") == "No":
                self.outcome = "canceled"
        for e in u.get("sql_errors") or []:
            yield from self._retry(e, MAX_SQL_ATTEMPTS)
        for e in u.get("errors") or []:
            yield from self._retry(e, MAX_ETL_ATTEMPTS)
        if node == "execute_sql" and "result_columns" in u:
            yield self.event("rows", columns=u["result_columns"],
                             rows=[[json_value(v) for v in row] for row in u["result_rows"]],
                             row_count=u["result_row_count"], truncated=u["result_truncated"],
                             shown=min(u["result_row_count"], ROW_LIMIT))
        if node == "design_chart":
            yield self.event("chart", spec=u.get("chart_spec"), note=u.get("chart_note", ""),
                             **({"error": u["chart_error"]} if u.get("chart_error") else {}))
        if node == "write_output" and u.get("output"):
            o = u["output"]
            yield self.event("output", name=o["path"].replace("\\", "/").rsplit("/", 1)[-1],
                             table=o.get("table"), rows=o["rows"], columns=o["columns"])
        if node == "report_failure":
            self.outcome = "error"
            code, detail = self.error
            # Each agent's report opens with its plain-English headline; reuse it rather than copy the strings.
            headline = (u.get("final_answer") or "").split("\n\n", 1)[0]
            yield self.event("error", code=code or "run.failed", detail=detail, attempts=self.attempts,
                             headline=headline)

    def _retry(self, entry: dict, limit: int) -> Iterator[dict]:
        self.attempts = max(self.attempts, entry.get("attempt", 0))
        if self.error[0] in _RETRYABLE and entry.get("attempt", 0) < limit:
            yield self.event("retry", attempt=entry["attempt"], of=limit, code=entry.get("code", ""),
                             detail=entry.get("detail", ""))

    def _agent_result(self, update: dict) -> Iterator[dict]:
        # The agent's final message; already streamed token by token for plain SQL answers.
        messages = update.get("messages") or []
        if messages and not self.answered and self.outcome != "error":
            self.answered = True
            yield self.event("answer", text=text_of(messages[-1]), done=False)

    def finish(self) -> Iterator[dict]:
        if self.outcome != "clarify":
            if self.answered:
                yield self.event("answer", text="", done=True)
            if self.model and self.answered:
                yield self.event("provider", provider=_PROVIDER_OF_MODEL.get(self.model, ""), model=self.model,
                                 # Judged against the first model with a key, so a one-provider setup isn't all "fallback".
                                 fallback=self.model != first_model())
        yield self.event("done", elapsed=self.elapsed(), outcome=self.outcome)
