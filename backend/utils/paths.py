from pathlib import Path

from utils.config import PROJECT_ROOT
from utils.result import Result


def resolve_under_root(candidate: str) -> Result[Path]:
    """Resolve a path relative to PROJECT_ROOT, refusing anything that escapes it."""
    try:
        resolved = (PROJECT_ROOT / candidate).resolve()
    except (OSError, ValueError) as exc:
        return Result.fail("path.invalid", f"Cannot resolve {candidate!r}: {exc}")

    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return Result.fail(
            "path.escape",
            f"{candidate!r} resolves to {resolved}, outside the project root {PROJECT_ROOT}.",
        )

    return Result.ok(resolved)
