import os
import subprocess
import sys
import tempfile
from pathlib import Path

from utils.result import Result

# Cheap pre-filter for obviously wrong generations. The subprocess is the real boundary.
_BANNED = (
    "__import__", "eval(", "exec(", "compile(", "subprocess", "os.system",
    "os.popen", "socket", "shutil.rmtree", "os.environ", "getattr(",
)

# Only what Python and pandas need to start. An allowlist, so DATABASE_* and every API key are absent.
_SAFE_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LOCALAPPDATA", "APPDATA",
)


def run_generated_code(code: str, workdir: Path, timeout: float = 60.0) -> Result[str]:
    """Run LLM-generated Python in an isolated child process with no credentials."""
    hits = [token for token in _BANNED if token in code]
    if hits:
        return Result.fail(
            "sandbox.rejected",
            f"Generated code uses disallowed constructs {hits}. "
            f"Rewrite using only pandas and plain Python.",
        )

    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "generated.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=str(workdir),
                env=env,
                timeout=timeout,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return Result.fail(
                "sandbox.timeout", f"Generated code ran longer than {timeout:.0f}s and was killed."
            )
        except OSError as exc:
            return Result.fail("sandbox.spawn_failed", str(exc))

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-2000:] or f"exit code {proc.returncode}"
        return Result.fail("sandbox.runtime_error", detail)

    return Result.ok((proc.stdout or "").strip() or "(code ran, produced no output)")
