import logging
import os
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from utils.config import require
from utils.ratelimit import Quota, UsageMeter

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Reasoning models think before they answer, and when the output cap is smaller than
# the thinking the reply comes back silently empty (finish_reason=length). Groq
# defaults to 2048 and Cloudflare to 256, both too low, so the cap is set explicitly.
MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "8192"))

HIGH_REASONING_EFFORT = os.environ.get("HIGH_REASONING_EFFORT", "high")

# Seconds. A healthy Gemini answers in 1-4s; reasoning calls legitimately take 10-20s.
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "20"))
GEMINI_REASONING_TIMEOUT = float(os.environ.get("GEMINI_REASONING_TIMEOUT", "90"))

# After a timeout, 503 or 429, skip that model for a while, so only the first call
# during an outage pays the timeout and the rest go straight to the next provider.
# Doubles on each consecutive failure, so a long outage is probed rarely.
COOLDOWN_SECONDS = float(os.environ.get("MODEL_COOLDOWN_SECONDS", "60"))
MAX_COOLDOWN_SECONDS = float(os.environ.get("MODEL_MAX_COOLDOWN_SECONDS", "900"))
# Gemini reports its own timeout as 504 DEADLINE_EXCEEDED, not as a timeout error.
_TRANSIENT_MARKERS = (
    "timeout", "timed out", "deadline", "502", "503", "504", "unavailable",
    "429", "resource_exhausted", "overloaded", "rate limit", "high demand",
)
_COOLDOWN_UNTIL: dict[str, float] = {}
_FAILURE_STREAK: dict[str, int] = {}


class ModelCoolingDown(RuntimeError):
    """Raised to skip a model that recently failed for provider-side reasons."""


class _HideExpectedSkips(logging.Filter):
    """LangChain logs every exception raised in a callback. Skipping a cooling or
    spent model is normal control flow, not an error, so keep it out of the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not ("ModelCoolingDown" in message or "QuotaExceeded" in message)


logging.getLogger("langchain_core.callbacks.manager").addFilter(_HideExpectedSkips())


def is_transient(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)

def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


# Newest Gemini releases as of Sep 2026; override in .env when newer ones ship.
# 3.8 Flash reasons by default, so the fast chain asks it for low effort.
GEMINI_LITE_MODEL = os.environ.get("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite")
GEMINI_FLASH_MODEL = os.environ.get("GEMINI_FLASH_MODEL", "gemini-3.8-flash")

# Gemini free-tier limits differ per model and apply per Google Cloud project, not per
# key. Google shows the real numbers only in AI Studio (aistudio.google.com/rate-limit);
# these defaults come from third-party guides (Sep 2026), so check and override them.
_GEMINI_QUOTAS = {
    GEMINI_LITE_MODEL: Quota(
        rpm=_env_int("GEMINI_LITE_RPM", 15),
        tpm=_env_int("GEMINI_LITE_TPM", 250_000),
        rpd=_env_int("GEMINI_LITE_RPD", 500),
        reset_tz="America/Los_Angeles",
    ),
    GEMINI_FLASH_MODEL: Quota(
        rpm=_env_int("GEMINI_FLASH_RPM", 10),
        tpm=_env_int("GEMINI_FLASH_TPM", 250_000),
        rpd=_env_int("GEMINI_FLASH_RPD", 20),
        reset_tz="America/Los_Angeles",
    ),
}

# Groq free tier, applied separately to each model (confirmed from its response headers).
_GROQ_QUOTA = Quota(
    rpm=_env_int("GROQ_RPM", 30),
    tpm=_env_int("GROQ_TPM", 8_000),
    rpd=_env_int("GROQ_RPD", 1_000),
    tpd=_env_int("GROQ_TPD", 200_000),
)

# One shared free allocation in neurons, not requests: a high-effort gpt-oss-120b call
# costs ~130 neurons where llama-4-scout costs ~6. Resets at midnight UTC.
_CLOUDFLARE_QUOTA = Quota(
    rpm=_env_int("CLOUDFLARE_RPM", 60),
    tpm=10**12,
    rpd=10**12,
    tpd=_env_int("CLOUDFLARE_NEURONS_PER_DAY", 10_000),
    unit="neurons",
)

# Each level is a preference order of (provider, model, reasoning_effort).
# Groq first: it answers in about a second, while Gemini's free tier often answers in
# 30s+ or returns 503 under load, so Gemini is the last resort. qwen3.8-27b is the
# fastest and most token-efficient Groq model, which matters on a token budget.
_FAST_CHAIN = [
    ("groq", "qwen/qwen3.8-27b", None),
    ("groq", "openai/gpt-oss-20b", "low"),
    ("cloudflare", "@cf/meta/llama-4-scout-17b-16e-instruct", None),
    ("gemini", GEMINI_LITE_MODEL, None),
    ("gemini", GEMINI_FLASH_MODEL, "low"),
]

# "high" drives code generation and tool use, so every tier is a reasoning model.
_REASONING_CHAIN = [
    ("groq", "openai/gpt-oss-120b", HIGH_REASONING_EFFORT),
    ("cloudflare", "@cf/openai/gpt-oss-120b", HIGH_REASONING_EFFORT),
    ("gemini", GEMINI_FLASH_MODEL, HIGH_REASONING_EFFORT),
]

CHAINS = {"low": _FAST_CHAIN, "medium": _FAST_CHAIN, "high": _REASONING_CHAIN}


def _meter_key(provider: str, model: str) -> str:
    # Groq and Gemini budget each model separately; Cloudflare shares one allowance.
    if provider == "groq":
        return f"groq-{model}"
    if provider == "gemini":
        return model
    return provider


def _quota_for(provider: str, model: str) -> Quota:
    if provider == "gemini":
        # A model named in .env but not listed above gets the stricter Flash limits.
        return _GEMINI_QUOTAS.get(model, _GEMINI_QUOTAS[GEMINI_FLASH_MODEL])
    return _GROQ_QUOTA if provider == "groq" else _CLOUDFLARE_QUOTA


_METERS = {
    _meter_key(provider, model): UsageMeter(_meter_key(provider, model), _quota_for(provider, model))
    for chain in CHAINS.values()
    for provider, model, _ in chain
}


class _UsageCallback(BaseCallbackHandler):
    """Books each call against its budget, and skips models that are down or spent."""

    # Without this LangChain swallows QuotaExceeded and calls the provider anyway.
    raise_error = True

    def __init__(self, meter: UsageMeter, model: str, skippable: bool = True):
        self.meter = meter
        self.model = model
        # The last model in a chain is never skipped: nothing is behind it.
        self.skippable = skippable

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        if self.skippable and time.monotonic() < _COOLDOWN_UNTIL.get(self.model, 0.0):
            raise ModelCoolingDown(f"{self.model} failed recently; skipping it for now.")
        # ~4 characters per token is close enough to pace requests.
        self.meter.acquire(sum(len(p) for p in prompts) // 4)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        # A 400 means our request was wrong, not that the model is down, so only
        # provider-side failures start a cooldown.
        if is_transient(error):
            streak = _FAILURE_STREAK.get(self.model, 0) + 1
            _FAILURE_STREAK[self.model] = streak
            wait = min(COOLDOWN_SECONDS * 2 ** (streak - 1), MAX_COOLDOWN_SECONDS)
            _COOLDOWN_UNTIL[self.model] = time.monotonic() + wait

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        _FAILURE_STREAK.pop(self.model, None)
        usages = [(response.llm_output or {}).get("token_usage") or {}]
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usages.append((getattr(message, "response_metadata", None) or {}).get("token_usage") or {})
                usages.append(getattr(message, "usage_metadata", None) or {})

        key = "neurons" if self.meter.quota.unit == "neurons" else "total_tokens"
        used = next((u[key] for u in usages if u.get(key) is not None), 0)
        self.meter.record(round(used))


def _build(
    provider: str, model: str, effort: str | None, temperature: float,
    retries: int = 0, last: bool = True,
):
    callbacks = [_UsageCallback(_METERS[_meter_key(provider, model)], model, skippable=not last)]
    reasoning = {"reasoning_effort": effort} if effort else {}

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # The SDK's default is no timeout. An overloaded Gemini answers in 40s+
        # instead of failing, so without one the chain never reaches Groq.
        timeout = GEMINI_REASONING_TIMEOUT if effort else GEMINI_TIMEOUT
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=require("GEMINI_API_KEY"),
            callbacks=callbacks,
            max_retries=retries,
            timeout=timeout,
            **reasoning,
        )

    # Groq and Workers AI both expose OpenAI-compatible endpoints.
    from langchain_openai import ChatOpenAI

    if provider == "groq":
        api_key, base_url, timeout = require("GROQ_API_KEY"), GROQ_BASE_URL, 90
    else:
        account_id = require("CLOUDFLARE_ACCOUNT_ID")
        api_key = require("CLOUDFLARE_API_TOKEN")
        base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        timeout = 120

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
        callbacks=callbacks,
        max_retries=retries,
        timeout=timeout,
        max_tokens=MAX_OUTPUT_TOKENS,
        **reasoning,
    )


_PROVIDER_KEYS = {
    "groq": ("GROQ_API_KEY",),
    "cloudflare": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"),
    "gemini": ("GEMINI_API_KEY",),
}


class NoProviderConfigured(RuntimeError):
    """No model in the chain has its API key set."""


def configured(provider: str) -> bool:
    return all(os.environ.get(key) for key in _PROVIDER_KEYS[provider])


def _chain_for(level: str) -> list[tuple[str, str, str | None]]:
    chain = CHAINS[level]
    # e.g. OMNIQUERY_PROVIDER=groq to test one provider on its own.
    forced = os.environ.get("OMNIQUERY_PROVIDER")
    if forced:
        chain = [entry for entry in chain if entry[0] == forced]
        if not chain:
            raise ValueError(f"OMNIQUERY_PROVIDER={forced} has no models for level {level!r}.")
    # Only one free key is needed to start; providers without keys are left out.
    chain = [entry for entry in chain if configured(entry[0])]
    if not chain:
        raise NoProviderConfigured("No model provider is configured. Set GROQ_API_KEY (free) in backend/.env.")
    return chain


def first_model(level: str = "low") -> str | None:
    """The model a call tries first with the keys that are set, or None if there are none."""
    try:
        return _chain_for(level)[0][1]
    except (NoProviderConfigured, ValueError):
        return None


def pick_llm(level: str, temperature: float = 0.0, tools: list | None = None):
    """Chat model for a level: Groq, then Cloudflare Workers AI, then Gemini.

    A 429, a 503 or an exhausted daily budget raises, which moves the call down
    the chain. Tools are bound here so every model in the chain gets them.
    """
    level = level.lower()
    if level not in CHAINS:
        raise ValueError("Invalid level. Choose from low, medium, or high.")

    chain = _chain_for(level)
    built = []
    for i, (provider, model, effort) in enumerate(chain):
        # The chain is the retry strategy, so retrying a failing model only delays
        # the fallback. The last model keeps one retry: it has nothing behind it.
        last = i == len(chain) - 1
        candidate = _build(provider, model, effort, temperature, retries=1 if last else 0, last=last)
        built.append(candidate.bind_tools(tools) if tools else candidate)

    return built[0] if len(built) == 1 else built[0].with_fallbacks(built[1:])


def text_of(message, strip: bool = True) -> str:
    """Plain text from a reply. Gemini returns content blocks, others return a string.

    strip=False keeps edge spaces, which streamed pieces of one answer need.
    """
    accessor = getattr(message, "text", None)
    if isinstance(accessor, str):
        text = accessor
    elif callable(accessor):
        text = accessor()
    elif isinstance(message.content, str):
        text = message.content
    else:
        text = "".join(block.get("text", "") for block in message.content if isinstance(block, dict))
    return text.strip() if strip else text


def usage_report() -> list[dict]:
    return [meter.snapshot() for meter in _METERS.values()]
