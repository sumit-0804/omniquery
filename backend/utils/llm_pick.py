import os
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

QUOTAS = {
    "gemini": Quota(
        rpm=int(os.environ.get("GEMINI_RPM", "12")),
        tpm=int(os.environ.get("GEMINI_TPM", "200000")),
        rpd=int(os.environ.get("GEMINI_RPD", "900")),
    ),
    # Groq free tier, applied separately to each model: its limits are per model.
    "groq": Quota(
        rpm=int(os.environ.get("GROQ_RPM", "30")),
        tpm=int(os.environ.get("GROQ_TPM", "8000")),
        rpd=int(os.environ.get("GROQ_RPD", "1000")),
        tpd=int(os.environ.get("GROQ_TPD", "200000")),
    ),
    # One shared free allocation in neurons, not requests: a high-effort
    # gpt-oss-120b call costs ~130 neurons where llama-4-scout costs ~6.
    "cloudflare": Quota(
        rpm=int(os.environ.get("CLOUDFLARE_RPM", "60")),
        tpm=10**12,
        rpd=10**12,
        tpd=int(os.environ.get("CLOUDFLARE_NEURONS_PER_DAY", "10000")),
        unit="neurons",
    ),
}

# Each level is a preference order of (provider, model, reasoning_effort).
# flash-lite is far less likely to return 503 than full flash; qwen3.8-27b is the
# fastest and most token-efficient Groq model, which matters on a token budget.
_FAST_CHAIN = [
    ("gemini", "gemini-3.5-flash-lite", None),
    ("gemini", "gemini-3.1-flash-lite", None),
    ("groq", "qwen/qwen3.8-27b", None),
    ("groq", "openai/gpt-oss-20b", "low"),
    ("cloudflare", "@cf/meta/llama-4-scout-17b-16e-instruct", None),
]

# "high" drives code generation and tool use, so every tier is a reasoning model.
_REASONING_CHAIN = [
    ("gemini", "gemini-3.5-flash", HIGH_REASONING_EFFORT),
    ("groq", "openai/gpt-oss-120b", HIGH_REASONING_EFFORT),
    ("cloudflare", "@cf/openai/gpt-oss-120b", HIGH_REASONING_EFFORT),
]

CHAINS = {"low": _FAST_CHAIN, "medium": _FAST_CHAIN, "high": _REASONING_CHAIN}


def _meter_key(provider: str, model: str) -> str:
    return f"groq-{model}" if provider == "groq" else provider


_METERS = {
    _meter_key(provider, model): UsageMeter(_meter_key(provider, model), QUOTAS[provider])
    for chain in CHAINS.values()
    for provider, model, _ in chain
}


class _UsageCallback(BaseCallbackHandler):
    """Books each call against its budget, and blocks or raises when spent."""

    # Without this LangChain swallows QuotaExceeded and calls the provider anyway.
    raise_error = True

    def __init__(self, meter: UsageMeter):
        self.meter = meter

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        # ~4 characters per token is close enough to pace requests.
        self.meter.acquire(sum(len(p) for p in prompts) // 4)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usages = [(response.llm_output or {}).get("token_usage") or {}]
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usages.append((getattr(message, "response_metadata", None) or {}).get("token_usage") or {})
                usages.append(getattr(message, "usage_metadata", None) or {})

        key = "neurons" if self.meter.quota.unit == "neurons" else "total_tokens"
        used = next((u[key] for u in usages if u.get(key) is not None), 0)
        self.meter.record(round(used))


def _build(provider: str, model: str, effort: str | None, temperature: float):
    callbacks = [_UsageCallback(_METERS[_meter_key(provider, model)])]
    reasoning = {"reasoning_effort": effort} if effort else {}

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=require("GEMINI_API_KEY"),
            callbacks=callbacks,
            max_retries=1,
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
        max_retries=1,
        timeout=timeout,
        max_tokens=MAX_OUTPUT_TOKENS,
        **reasoning,
    )


def _chain_for(level: str) -> list[tuple[str, str, str | None]]:
    chain = CHAINS[level]
    # e.g. OMNIQUERY_PROVIDER=groq to test one provider on its own.
    forced = os.environ.get("OMNIQUERY_PROVIDER")
    if forced:
        chain = [entry for entry in chain if entry[0] == forced]
        if not chain:
            raise ValueError(f"OMNIQUERY_PROVIDER={forced} has no models for level {level!r}.")
    return chain


def pick_llm(level: str, temperature: float = 0.0, tools: list | None = None):
    """Chat model for a level: Gemini, then Groq, then Cloudflare Workers AI.

    A 429, a 503 or an exhausted daily budget raises, which moves the call down
    the chain. Tools are bound here so every model in the chain gets them.
    """
    level = level.lower()
    if level not in CHAINS:
        raise ValueError("Invalid level. Choose from low, medium, or high.")

    built = []
    for provider, model, effort in _chain_for(level):
        candidate = _build(provider, model, effort, temperature)
        built.append(candidate.bind_tools(tools) if tools else candidate)

    return built[0] if len(built) == 1 else built[0].with_fallbacks(built[1:])


def text_of(message) -> str:
    """Plain text from a reply. Gemini returns content blocks, others return a string."""
    accessor = getattr(message, "text", None)
    if isinstance(accessor, str):
        return accessor.strip()
    if callable(accessor):
        return accessor().strip()

    content = message.content
    if isinstance(content, str):
        return content.strip()
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    ).strip()


def usage_report() -> list[dict]:
    return [meter.snapshot() for meter in _METERS.values()]
