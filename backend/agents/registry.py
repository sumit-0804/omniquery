from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from agents.etl_analyst import etl_analyst
from agents.sql_analyst import sql_analyst
from utils.llm_pick import text_of


@dataclass(frozen=True)
class AgentSpec:
    key: str
    description: str
    graph: Any
    build_input: Callable[[str], dict]
    extract_answer: Callable[[dict], str]

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
            build_input=lambda message: {"user_question": message},
            extract_answer=lambda result: result.get("final_answer") or _last_message(result),
        ),
        AgentSpec(
            key="etl",
            description="Extract data from an API or file, transform it, or load it somewhere",
            graph=etl_analyst,
            build_input=lambda message: {"messages": [HumanMessage(content=message)]},
            extract_answer=_last_message,
        ),
    )
}

AGENT_KEYS = tuple(REGISTRY)
