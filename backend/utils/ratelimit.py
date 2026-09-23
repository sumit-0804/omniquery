import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date

from utils.config import PROJECT_ROOT

_STATE_DIR = PROJECT_ROOT / ".quota"


class QuotaExceeded(RuntimeError):
    """Raised when a daily budget is spent."""


@dataclass(frozen=True)
class Quota:
    rpm: int
    tpm: int
    rpd: int
    # Daily usage cap, counted in `unit`: tokens, or neurons for Cloudflare Workers AI.
    tpd: int = 10**12
    unit: str = "tokens"


class UsageMeter:
    """Enforces requests/min, tokens/min, requests/day and usage/day for one budget.

    Per-minute windows live in memory; the daily count is persisted so it
    survives restarts, which is the whole point of a daily cap.
    """

    def __init__(self, name: str, quota: Quota):
        self.name = name
        self.quota = quota
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        # Model ids such as "openai/gpt-oss-120b" are not valid file names.
        self._state_path = _STATE_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.json"
        self._day, self._day_requests, self._day_tokens = self._load()

    def _load(self) -> tuple[str, int, int]:
        today = date.today().isoformat()
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return today, 0, 0
        if saved.get("day") != today:
            return today, 0, 0
        return today, int(saved.get("requests", 0)), int(saved.get("tokens", 0))

    def _save(self) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(
                    {"day": self._day, "requests": self._day_requests, "tokens": self._day_tokens}
                ),
                encoding="utf-8",
            )
        except OSError:
            # Losing the daily counter must never break a query.
            pass

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            self._day, self._day_requests, self._day_tokens = today, 0, 0

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self._requests and self._requests[0] <= cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= cutoff:
            self._tokens.popleft()

    def acquire(self, estimated_tokens: int = 0) -> None:
        """Block until this call fits inside the per-minute limits."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._roll_day()
                self._prune(now)

                if self._day_requests >= self.quota.rpd:
                    raise QuotaExceeded(
                        f"{self.name}: daily limit of {self.quota.rpd} requests reached. "
                        f"It resets at midnight local time."
                    )
                if self._day_tokens >= self.quota.tpd:
                    raise QuotaExceeded(
                        f"{self.name}: daily limit of {self.quota.tpd:,} {self.quota.unit} "
                        f"reached. It resets at midnight local time."
                    )

                waits = []
                if len(self._requests) >= self.quota.rpm:
                    waits.append(self._requests[0] + 60.0 - now)
                used_tokens = sum(t for _, t in self._tokens)
                if used_tokens + estimated_tokens > self.quota.tpm and self._tokens:
                    waits.append(self._tokens[0][0] + 60.0 - now)

                if not waits:
                    self._requests.append(now)
                    self._day_requests += 1
                    self._save()
                    return

                delay = max(min(waits), 0.05)

            time.sleep(min(delay, 5.0))

    def record(self, tokens: int) -> None:
        with self._lock:
            self._roll_day()
            now = time.monotonic()
            self._prune(now)
            self._tokens.append((now, tokens))
            self._day_tokens += tokens
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            return {
                "provider": self.name,
                "rpm_used": len(self._requests),
                "rpm_limit": self.quota.rpm,
                "tpm_used": sum(t for _, t in self._tokens),
                "tpm_limit": self.quota.tpm,
                "rpd_used": self._day_requests,
                "rpd_limit": self.quota.rpd,
                "day_used": self._day_tokens,
                "day_limit": self.quota.tpd,
                "unit": self.quota.unit,
            }
