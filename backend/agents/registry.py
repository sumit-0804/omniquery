from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents.chart_analyst import CHART_ROW_LIMIT, chart_analyst
from agents.etl_analyst import etl_analyst
from agents.sql_analyst import sql_analyst
from utils.llm_pick import text_of


@dataclass(frozen=True)
class AgentSpec:
    key: str
    description: str
    graph: Any
    build_input: Callable[[str, str], dict]  # (message, source_id)
    extract_answer: Callable[[dict], str]
    # Extra results passed up to the data agent's state, e.g. a chart spec.
    extract_extra: Callable[[dict], dict] | None = None

    @property
    def node_name(self) -> str:
        return f"{self.key}_node"


def _last_message(result: dict) -> str:
    messages = result.get("messages") or []
    return text_of(messages[-1]) if messages else "The agent returned no answer."


REGISTRY: dict[str, AgentSpec] = {
    spec.key: spec
    for spec in (
        AgentSpec(
            key="sql",
            description="Query, aggregate, or analyse data already stored in the database",
            graph=sql_analyst,
            build_input=lambda message, source_id: {"user_question": message, "source_id": source_id},
            extract_answer=lambda result: result.get("final_answer") or _last_message(result),
        ),
        AgentSpec(
            key="etl",
            description=(
                "Extract data from a URL or API, or transform data (filter, reshape, clean, "
                "join, convert) and save the result as a new file"
            ),
            graph=etl_analyst,
            build_input=lambda message, source_id: {"user_request": message, "source_id": source_id},
            extract_answer=lambda result: result.get("final_answer") or "The ETL agent returned no answer.",
        ),
        AgentSpec(
            key="chart",
            description="Draw a chart, plot or graph of data from the database; only when the user asks for a visual",
            graph=chart_analyst,
            build_input=lambda message, source_id: {
                "user_question": message, "source_id": source_id, "row_limit": CHART_ROW_LIMIT,
            },
            extract_answer=lambda result: result.get("final_answer") or _last_message(result),
            extract_extra=lambda result: {
                "chart_spec": result.get("chart_spec"),
                "chart_error": result.get("chart_error", ""),
                "chart_note": result.get("chart_note", ""),
            },
        ),
    )
}

AGENT_KEYS = tuple(REGISTRY)
