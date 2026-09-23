from pathlib import Path

from utils.config import PROJECT_ROOT
from utils.result import Result


def save_graph_png(compiled_graph, filename: str) -> Result[Path]:
    """Render a graph to PNG via mermaid.ink. Network-dependent, so never fatal."""
    target = PROJECT_ROOT / filename
    try:
        target.write_bytes(compiled_graph.get_graph().draw_mermaid_png())
    except Exception as exc:
        return Result.fail("viz.unavailable", f"Could not render {filename}: {exc}")
    return Result.ok(target)
