"""The gift-card guessing budget counts requests that arrive together.

DRF's throttle reads a caller's history, compares it with the limit and
writes it back as three steps. Requests that arrive together all read the
same history, all pass, and each writes a history that leaves the others
out: twelve parallel guesses were twelve answers on a budget of six. The
gift-card budgets do the three steps while holding the budget's key.
"""

from __future__ import annotations

import threading

import pytest
from rest_framework.test import APIRequestFactory

from giftcard.throttles import GiftCardCheckThrottle, GiftCardCodeThrottle
from tests.utils.redis import private_cache

LIMIT = 6
CALLERS = 12


def _request():
    request = APIRequestFactory().post("/x", REMOTE_ADDR="203.0.113.7")
    request.user = type("Anon", (), {"is_authenticated": False, "pk": None})()
    return request


def _together(throttles) -> list[bool]:
    """Ask every throttle at the same moment, one thread each."""
    start = threading.Barrier(len(throttles))
    answers: list[bool] = []

    def ask(throttle):
        start.wait()
        answers.append(throttle.allow_request(_request(), None))

    threads = [threading.Thread(target=ask, args=(t,)) for t in throttles]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return answers


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setattr(
        GiftCardCheckThrottle, "cache", private_cache(), raising=False
    )
    monkeypatch.setattr(
        GiftCardCheckThrottle, "rate", f"{LIMIT}/minute", raising=False
    )


@pytest.mark.usefixtures("budget")
def test_parallel_balance_checks_spend_the_budget_once_each():
    answers = _together([GiftCardCheckThrottle() for _ in range(CALLERS)])

    assert answers.count(True) == LIMIT


@pytest.mark.usefixtures("budget")
def test_parallel_guesses_of_new_codes_spend_it_once_each():
    answers = _together(
        [GiftCardCodeThrottle([f"GC-GUESS-{n:04d}"]) for n in range(CALLERS)]
    )

    assert answers.count(True) == LIMIT


@pytest.mark.usefixtures("budget")
def test_a_request_with_several_new_codes_takes_them_together():
    """Two codes each: three requests fit a budget of six, never four
    (which a check made before the others counted would let through)."""
    answers = _together(
        [
            GiftCardCodeThrottle([f"GC-A-{n:04d}", f"GC-B-{n:04d}"])
            for n in range(CALLERS)
        ]
    )

    assert answers.count(True) == LIMIT // 2
