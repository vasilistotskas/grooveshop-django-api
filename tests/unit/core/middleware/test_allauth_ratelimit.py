"""The allauth rate limiter, with Redis up and with Redis down.

With Redis unreachable the limits must not lift: the 2FA and login-code
routes would lose their per-address bound exactly when someone can make
Redis slow. The middleware answers 503 ``rate_limit_unavailable`` in
allauth's envelope instead, for its own counters and for the strict keys
allauth's views read (their own limits, TOTP replay, the IdP's codes).
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from core.caches import STRICT_NAMESPACE
from core.middleware import allauth_ratelimit
from core.middleware.allauth_ratelimit import AllAuthRateLimitMiddleware
from tests.utils.redis import stopped_cache

TWO_FACTOR = "/_allauth/app/v1/auth/2fa/authenticate"
LOGIN = "/_allauth/app/v1/auth/login"


def _middleware(get_response=None) -> AllAuthRateLimitMiddleware:
    middleware = AllAuthRateLimitMiddleware(
        get_response or (lambda request: HttpResponse("view"))
    )
    # The suite runs with DISABLE_CACHE, which switches the counting off.
    middleware.disabled = False
    return middleware


def _post(path: str, address: str = "203.0.113.7"):
    return RequestFactory().post(path, REMOTE_ADDR=address)


def _assert_unavailable(response) -> None:
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body["status"] == 503
    assert [error["code"] for error in body["errors"]] == [
        "rate_limit_unavailable"
    ]
    assert body["errors"][0]["message"]


@pytest.fixture(autouse=True)
def _fresh_burst(monkeypatch):
    original = allauth_ratelimit._refused_while_unavailable
    monkeypatch.setattr(
        allauth_ratelimit,
        "_refused_while_unavailable",
        type(original)(
            original._message, level=original._level, target=original._logger
        ),
    )


class TestRedisDown:
    def test_a_limited_route_is_refused_with_a_coded_503(self, monkeypatch):
        monkeypatch.setattr(allauth_ratelimit, "cache", stopped_cache())
        view = MagicMock(return_value=HttpResponse("view"))

        response = _middleware(view)(_post(TWO_FACTOR))

        _assert_unavailable(response)
        view.assert_not_called()

    def test_the_refusal_is_logged_as_an_error(self, monkeypatch, caplog):
        monkeypatch.setattr(allauth_ratelimit, "cache", stopped_cache())

        with caplog.at_level("ERROR", logger=allauth_ratelimit.__name__):
            _middleware()(_post(LOGIN))

        assert any("REFUSED" in r.getMessage() for r in caplog.records)

    def test_a_route_without_a_limit_is_not_counted(self, monkeypatch):
        monkeypatch.setattr(allauth_ratelimit, "cache", stopped_cache())

        response = _middleware()(_post("/_allauth/app/v1/auth/session"))

        assert response.content == b"view"

    @pytest.mark.parametrize(
        "error", [RedisConnectionError("down"), RedisTimeoutError("slow")]
    )
    def test_allauths_own_strict_keys_answer_the_same_503(self, error):
        response = _middleware().process_exception(_post(LOGIN), error)

        _assert_unavailable(response)

    def test_an_outage_outside_allauth_is_left_to_its_own_view(self):
        request = _post("/api/v1/cart")

        assert (
            _middleware().process_exception(
                request, RedisConnectionError("down")
            )
            is None
        )

    def test_any_other_error_stays_an_error(self):
        assert (
            _middleware().process_exception(_post(LOGIN), ValueError("bug"))
            is None
        )


class TestRedisUp:
    def test_the_counters_are_strict_keys(self, monkeypatch):
        counted: list[str] = []

        class _Recorder:
            def count_in_window(self, key, timeout):
                counted.append(key)
                return 1

        monkeypatch.setattr(allauth_ratelimit, "cache", _Recorder())

        _middleware()(_post(TWO_FACTOR))

        assert counted
        assert all(key.startswith(STRICT_NAMESPACE) for key in counted)

    def test_the_limit_refuses_once_spent(self):
        middleware = _middleware()
        # A fresh address per run: the counters live in the suite's Redis
        # and outlast the test by their window.
        address = str(ipaddress.IPv6Address(uuid.uuid4().int))
        allowed = [
            middleware(_post(TWO_FACTOR, address)).status_code
            for _ in range(10)
        ]

        response = middleware(_post(TWO_FACTOR, address))

        assert allowed == [200] * 10
        assert response.status_code == 429
        assert json.loads(response.content)["errors"][0]["code"] == (
            "too_many_requests"
        )
