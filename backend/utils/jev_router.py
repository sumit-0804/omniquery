"""Routing decisions from Jev, TypeSafe's hosted decision model, reached through OpenRouter.

One choice question: which agent, or "ambiguous". Built on first use, so importing the
agents makes no network call and needs no key.
"""

import os
import threading
import warnings

from langchain_typesafe import Choice, TypeSafeClassifier

# The class works as used here; the "in beta" notice would print on every CLI run.
warnings.filterwarnings("ignore", message="The class `TypeSafeClassifier` is in beta")

# Jev is served through OpenRouter's System One API, so it reuses the OpenRouter key.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
JEV_MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
AMBIGUOUS = "ambiguous"

_classifier: TypeSafeClassifier | None = None
_lock = threading.Lock()


def _load() -> TypeSafeClassifier:
    global _classifier
    with _lock:
        if _classifier is None:
            _classifier = TypeSafeClassifier(
                model=JEV_MODEL,
                api_key=os.environ["OPENROUTER_API_KEY"],
                base_url=OPENROUTER_BASE_URL,
                timeout=15.0,
            )
    return _classifier


def route_question(agents: dict[str, str]) -> dict:
    """The routing question: one option per agent (key -> description), plus ambiguous."""
    return {"route": Choice(
        instructions="Which data agent should handle this request?",
        criteria=agents | {AMBIGUOUS: (
            "Too vague to act on: it points back to something not shown, names no concrete "
            "data or task, or could reasonably mean more than one of the other options"
        )},
    )}


def decide(message: str, questions: dict) -> dict:
    """Jev's pick as {route, confidence}, so nothing else depends on its result format."""
    answer = _load().invoke({"state": message, "questions": questions}).choices["route"]
    return {"route": answer.choice, "confidence": float(answer.confidence)}
