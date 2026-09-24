"""Routing eval: does the router send each message to the right agent, or ask to clarify?

    uv run python evals/run_routing_eval.py

Messages labelled "clarify" are vague on purpose: the right outcome is asking the user.
Laya decides alone only above LAYA_TRUST; below it this makes one LLM call per message.
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from langchain_core.messages import HumanMessage

from agents.data_agent import ROUTE_QUESTIONS, route_edge, router_node
from agents.registry import REGISTRY
from Models.schema import DataAgentSchema
from utils.laya_router import decide

MESSAGES = Path(__file__).resolve().parent / "routing.json"
_LABEL_OF_NODE = {spec.node_name: key for key, spec in REGISTRY.items()} | {"ask_human": "clarify"}


def route(message: str) -> tuple[str, dict]:
    update = router_node(DataAgentSchema(messages=[HumanMessage(content=message)], source_id="eval"))
    state = DataAgentSchema(messages=[HumanMessage(content=message)], source_id="eval", **update)
    return _LABEL_OF_NODE[route_edge(state)], update


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()

    items = json.loads(MESSAGES.read_text(encoding="utf-8"))
    decide("warm up", ROUTE_QUESTIONS)  # load the model before timing anything

    by_label: dict[str, list[bool]] = defaultdict(list)
    sources, seconds, conf_right, conf_wrong = Counter(), [], [], []
    for item in items:
        started = time.perf_counter()
        got, update = route(item["message"])
        seconds.append(time.perf_counter() - started)
        right = got == item["label"]
        by_label[item["label"]].append(right)
        sources[update["route_source"]] += 1
        if update["route_source"] == "laya":
            (conf_right if right else conf_wrong).append(update["route_confidence"])
        print(f"  {'ok  ' if right else 'MISS'} {item['label']:<8} -> {got:<8} "
              f"{update['route_source']:<5} conf {update.get('route_confidence', 0):.2f}  "
              f"{item['message'][:60]}", flush=True)

    total = sum(sum(v) for v in by_label.values())
    print(f"\naccuracy  {total}/{len(items)} = {100 * total / len(items):.0f}%")
    for label, results in sorted(by_label.items()):
        print(f"  {label:<8} {sum(results)}/{len(results)}")
    print("decided by " + ", ".join(f"{k} {v}" for k, v in sources.most_common()))
    if conf_right:
        print(f"laya confidence  right {statistics.mean(conf_right):.2f}"
              + (f", wrong {statistics.mean(conf_wrong):.2f}" if conf_wrong else ""))
    print(f"p50 latency {statistics.median(seconds) * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
