"""Routing decisions from Laya, a local open-weights decision model (Jev-style questions).

The model loads on first use, not at import, so importing the agents never pulls in torch.
"""

import logging
import os
import threading
import warnings

LAYA_MODEL = os.environ.get("LAYA_MODEL", "convaiinnovations/laya")

_log = logging.getLogger(__name__)
_agent = None
_lock = threading.Lock()


def _device() -> str:
    import torch

    wanted = os.environ.get("LAYA_DEVICE")
    if wanted:
        return wanted
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load():
    global _agent
    with _lock:
        if _agent is None:
            import laya

            # Only affects choices with 11+ options; routing has a handful.
            warnings.filterwarnings("ignore", message="laya: this checkpoint ships invalid temperatures")
            device = _device()
            _log.info("Loading Laya (%s) on %s; the first run downloads about 800 MB.", LAYA_MODEL, device)
            _agent = laya.load(LAYA_MODEL, device=device)
    return _agent


def to_decision(result: dict) -> dict:
    """Laya's answer as {route, confidence}, so nothing else depends on its format."""
    route = result["answers"]["route"]
    # answer_confidence is max(probabilities), the calibrated number Laya's thresholds are fit on.
    return {"route": route["choice"], "confidence": float(route["answer_confidence"])}


def decide(message: str, questions: dict) -> dict:
    return to_decision(_load().predict(message, questions))
