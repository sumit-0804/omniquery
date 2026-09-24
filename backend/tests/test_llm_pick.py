import pytest

import utils.llm_pick as lp
from utils.ratelimit import Quota


class _Meter:
    quota = Quota(rpm=10**6, tpm=10**9, rpd=10**6)

    def acquire(self, estimated_tokens=0):
        pass

    def record(self, tokens):
        pass


@pytest.fixture(autouse=True)
def _no_cooldowns():
    lp._COOLDOWN_UNTIL.clear()
    lp._FAILURE_STREAK.clear()
    yield
    lp._COOLDOWN_UNTIL.clear()
    lp._FAILURE_STREAK.clear()


@pytest.mark.parametrize("error", [
    TimeoutError("read timed out"),
    RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand"),
    RuntimeError("429 RESOURCE_EXHAUSTED"),
    RuntimeError("504 DEADLINE_EXCEEDED. Deadline expired before operation could complete."),
    RuntimeError("Error code: 429 - rate limit reached"),
])
def test_provider_side_failures_are_transient(error):
    assert lp._is_transient(error)


def test_a_bad_request_is_not_transient():
    assert not lp._is_transient(RuntimeError("400 INVALID_ARGUMENT: unknown field"))


def test_a_transient_failure_cools_the_model_down():
    callback = lp._UsageCallback(_Meter(), "model-a")
    callback.on_llm_error(TimeoutError("timed out"))

    with pytest.raises(lp.ModelCoolingDown):
        callback.on_llm_start({}, ["prompt"])


def test_a_bad_request_does_not_cool_the_model_down():
    callback = lp._UsageCallback(_Meter(), "model-a")
    callback.on_llm_error(RuntimeError("400 INVALID_ARGUMENT"))

    callback.on_llm_start({}, ["prompt"])


def test_the_last_model_in_a_chain_is_never_skipped():
    callback = lp._UsageCallback(_Meter(), "model-z", skippable=False)
    callback.on_llm_error(TimeoutError("timed out"))

    callback.on_llm_start({}, ["prompt"])


def test_the_cooldown_expires(monkeypatch):
    callback = lp._UsageCallback(_Meter(), "model-a")
    now = [1000.0]
    monkeypatch.setattr(lp.time, "monotonic", lambda: now[0])

    callback.on_llm_error(TimeoutError("timed out"))
    now[0] += lp.COOLDOWN_SECONDS + 1

    callback.on_llm_start({}, ["prompt"])


def test_consecutive_failures_double_the_cooldown_up_to_a_cap(monkeypatch):
    callback = lp._UsageCallback(_Meter(), "model-a")
    monkeypatch.setattr(lp.time, "monotonic", lambda: 0.0)

    waits = []
    for _ in range(8):
        callback.on_llm_error(TimeoutError("timed out"))
        waits.append(lp._COOLDOWN_UNTIL["model-a"])

    base = lp.COOLDOWN_SECONDS
    assert waits[:3] == [base, base * 2, base * 4]
    assert waits[-1] == lp.MAX_COOLDOWN_SECONDS


def test_a_success_resets_the_backoff(monkeypatch):
    from langchain_core.outputs import LLMResult

    callback = lp._UsageCallback(_Meter(), "model-a")
    monkeypatch.setattr(lp.time, "monotonic", lambda: 0.0)
    callback.on_llm_error(TimeoutError("timed out"))
    callback.on_llm_error(TimeoutError("timed out"))

    callback.on_llm_end(LLMResult(generations=[]))
    callback.on_llm_error(TimeoutError("timed out"))

    assert lp._COOLDOWN_UNTIL["model-a"] == lp.COOLDOWN_SECONDS


def test_expected_skips_are_kept_out_of_the_logs():
    import logging

    hide = lp._HideExpectedSkips()

    def record(message):
        return logging.LogRecord("x", logging.WARNING, "", 0, message, None, None)

    assert not hide.filter(record("Error in _UsageCallback.on_llm_start callback: ModelCoolingDown('m')"))
    assert not hide.filter(record("Error in _UsageCallback.on_llm_start callback: QuotaExceeded('q')"))
    assert hide.filter(record("Error in _UsageCallback.on_llm_end callback: KeyError('boom')"))


def test_cooldown_is_per_model():
    lp._UsageCallback(_Meter(), "model-a").on_llm_error(TimeoutError("timed out"))

    lp._UsageCallback(_Meter(), "model-b").on_llm_start({}, ["prompt"])


@pytest.fixture
def dummy_keys(monkeypatch):
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setenv(key, "dummy")
    monkeypatch.delenv("OMNIQUERY_PROVIDER", raising=False)


def _chain_models(runnable):
    return [runnable.runnable, *runnable.fallbacks]


def test_only_the_last_model_retries(dummy_keys):
    models = _chain_models(lp.pick_llm("medium"))

    assert [m.max_retries for m in models] == [0] * (len(models) - 1) + [1]


def _gemini(models):
    from langchain_google_genai import ChatGoogleGenerativeAI

    return [m for m in models if isinstance(m, ChatGoogleGenerativeAI)]


def test_gemini_has_a_timeout_and_reasoning_gets_longer(dummy_keys):
    lite = _gemini(_chain_models(lp.pick_llm("medium")))[0]
    reasoning = _gemini(_chain_models(lp.pick_llm("high")))[0]

    assert lite.timeout == lp.GEMINI_TIMEOUT
    assert reasoning.timeout == lp.GEMINI_REASONING_TIMEOUT


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_groq_comes_first_and_gemini_last(level):
    providers = [provider for provider, _, _ in lp.CHAINS[level]]

    assert providers[0] == "groq"
    assert providers[-1] == "gemini"
    assert providers.index("cloudflare") < providers.index("gemini")


def test_only_current_gemini_models_are_used():
    models = {model for chain in lp.CHAINS.values() for provider, model, _ in chain if provider == "gemini"}

    assert models == {lp.GEMINI_LITE_MODEL, lp.GEMINI_FLASH_MODEL}
    assert not any(m.startswith(("gemini-3.1", "gemini-3-", "gemini-2")) for m in models)


def test_each_gemini_model_has_its_own_budget_and_pacific_reset():
    lite = lp._METERS[lp._meter_key("gemini", lp.GEMINI_LITE_MODEL)]
    flash = lp._METERS[lp._meter_key("gemini", lp.GEMINI_FLASH_MODEL)]

    assert lite is not flash
    assert lite.quota.rpd != flash.quota.rpd
    assert lite.quota.reset_tz == flash.quota.reset_tz == "America/Los_Angeles"


def test_cloudflare_resets_at_utc_midnight():
    assert lp._METERS["cloudflare"].quota.reset_tz == "UTC"
