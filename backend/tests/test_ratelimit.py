from datetime import UTC, datetime

import pytest

from utils import ratelimit
from utils.ratelimit import Quota, QuotaExceeded, UsageMeter


@pytest.fixture(autouse=True)
def _state_in_tmp(tmp_path, monkeypatch):
    # The real meter persists to .quota/; keep tests out of it.
    monkeypatch.setattr(ratelimit, "_STATE_DIR", tmp_path)


def _freeze(monkeypatch, instant: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(ratelimit, "datetime", Frozen)


def test_the_day_follows_the_providers_reset_zone(monkeypatch):
    # 03:00 UTC on the 24th is still the 23rd in California and already the 24th in India.
    _freeze(monkeypatch, datetime(2026, 9, 24, 3, 0, tzinfo=UTC))

    pacific = UsageMeter("p", Quota(rpm=1, tpm=1, rpd=1, reset_tz="America/Los_Angeles"))
    utc = UsageMeter("u", Quota(rpm=1, tpm=1, rpd=1, reset_tz="UTC"))
    india = UsageMeter("i", Quota(rpm=1, tpm=1, rpd=1, reset_tz="Asia/Kolkata"))

    assert pacific._today() == "2026-09-23"
    assert utc._today() == "2026-09-24"
    assert india._today() == "2026-09-24"


def test_a_spent_budget_comes_back_at_the_providers_midnight(monkeypatch):
    quota = Quota(rpm=100, tpm=10**9, rpd=1, reset_tz="America/Los_Angeles")
    _freeze(monkeypatch, datetime(2026, 9, 23, 18, 0, tzinfo=UTC))  # 11:00 Pacific
    meter = UsageMeter("g", quota)
    meter.acquire()

    with pytest.raises(QuotaExceeded):
        meter.acquire()

    _freeze(monkeypatch, datetime(2026, 9, 24, 6, 59, tzinfo=UTC))  # 23:59 Pacific
    with pytest.raises(QuotaExceeded):
        meter.acquire()

    _freeze(monkeypatch, datetime(2026, 9, 24, 7, 1, tzinfo=UTC))  # 00:01 Pacific
    meter.acquire()


def test_an_unknown_zone_fails_at_startup():
    from zoneinfo import ZoneInfoNotFoundError

    with pytest.raises(ZoneInfoNotFoundError):
        UsageMeter("x", Quota(rpm=1, tpm=1, rpd=1, reset_tz="Mars/Olympus"))
