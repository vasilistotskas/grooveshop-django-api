"""What each throttle does while Redis is unreachable.

The default cache fails open, and DRF keeps throttle history with plain
``cache.get``/``cache.set``, so without this a Redis outage silently
lifted every rate limit — including the ones guarding a bearer secret
(gift cards), a code oracle (coupons), a third party (VIES) and money
(checkout). Security scopes now DENY with ``Retry-After``; general
scopes ALLOW, as a storefront must keep serving through a Redis blip.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from os import getenv

import pytest
from django.test import override_settings
from redis.backoff import NoBackoff
from redis.retry import Retry
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import (
    AnonRateThrottle,
    SimpleRateThrottle,
)
from rest_framework.views import APIView

from core import caches as core_caches
from core.api import throttling
from core.caches import STRICT_NAMESPACE, CustomCache

#: The classification this module locks in. Changing a scope's policy
#: must be a deliberate edit here too.
FAIL_CLOSED = {
    "PaymentAttemptThrottle",
    "PaymentAttemptAnonThrottle",
    "OrderCreateThrottle",
    "OrderCreateAnonThrottle",
    "CouponApplyThrottle",
    "GiftCardCheckThrottle",
    "B2BProfileSubmitThrottle",
    "NewsletterSubscribeThrottle",
    "ContactAttachmentThrottle",
    "VivaReturnThrottle",
    "AcsAddressValidationThrottle",
    "BoxNowNearestThrottle",
    "StaffLoginThrottle",
}
FAIL_OPEN = {
    "UserOrIpRateThrottle",
    "ContactCreateThrottle",
    "FeedbackCreateThrottle",
    "CartMutationThrottle",
    "CartMutationAnonThrottle",
    "SearchThrottle",
    "SearchClickThrottle",
    "ViewCountThrottle",
    "RecommendationEventThrottle",
}


def _stopped_cache() -> CustomCache:
    return CustomCache(
        server="redis://127.0.0.1:1/0",
        params={
            "OPTIONS": {
                "retry": Retry(NoBackoff(), 0),
                "socket_connect_timeout": 0.2,
                "health_check_interval": 0,
            }
        },
    )


def _live_cache() -> CustomCache:
    host = getenv("REDIS_HOST", "localhost")
    port = getenv("REDIS_PORT", "6379")
    worker = getenv("PYTEST_XDIST_WORKER", "gw0")
    db = (int("".join(filter(str.isdigit, worker)) or "0") % 15) + 1
    return CustomCache(
        server=f"redis://{host}:{port}/{db}",
        params={"KEY_PREFIX": f"throttle-test-{uuid.uuid4().hex[:8]}"},
    )


@pytest.fixture(autouse=True)
def _fresh_bursts(monkeypatch):
    for name in ("_denied_while_unavailable", "_allowed_while_unavailable"):
        original = getattr(throttling, name)
        monkeypatch.setattr(
            throttling,
            name,
            core_caches.BurstLogger(
                original._message,
                level=original._level,
                target=original._logger,
            ),
        )


def _request(path="/x"):
    request = APIRequestFactory().get(path, REMOTE_ADDR="203.0.113.7")
    request.user = type("Anon", (), {"is_authenticated": False, "pk": None})()
    return request


def _throttle(cls, monkeypatch, cache, rate="3/minute"):
    monkeypatch.setattr(cls, "cache", cache, raising=False)
    monkeypatch.setattr(cls, "rate", rate, raising=False)
    return cls()


class TestClassification:
    def test_every_throttle_here_declares_its_outage_policy(self):
        defined = {
            name: cls
            for name, cls in inspect.getmembers(throttling, inspect.isclass)
            if issubclass(cls, SimpleRateThrottle)
            and cls.__module__ == throttling.__name__
        }
        assert set(defined) == FAIL_CLOSED | FAIL_OPEN
        for name, cls in defined.items():
            assert issubclass(cls, throttling.ResilientThrottleMixin), name
            assert cls.fail_closed is (name in FAIL_CLOSED), name
            # The strict namespace is what makes the cache raise.
            assert cls.cache_format.startswith(STRICT_NAMESPACE), name


class TestRedisDown:
    def test_a_security_scope_denies_and_logs_an_error(
        self, monkeypatch, caplog
    ):
        throttle = _throttle(
            throttling.GiftCardCheckThrottle, monkeypatch, _stopped_cache()
        )

        with caplog.at_level(logging.ERROR, logger="core.api.throttling"):
            assert throttle.allow_request(_request(), view=None) is False

        # DRF turns a denial into 429 + Retry-After from ``wait()``.
        assert throttle.wait() > 0
        assert any(
            "gift_card_check" in r.getMessage() and "DENIED" in r.getMessage()
            for r in caplog.records
        )

    def test_a_general_scope_allows_and_warns(self, monkeypatch, caplog):
        throttle = _throttle(
            throttling.SearchThrottle, monkeypatch, _stopped_cache()
        )

        with caplog.at_level(logging.WARNING, logger="core.api.throttling"):
            assert throttle.allow_request(_request(), view=None) is True

        assert any("ALLOWED" in r.getMessage() for r in caplog.records)

    def test_drfs_own_default_throttles_stay_open(self, monkeypatch):
        """``anon``/``user`` are the day-scale browsing ceilings in
        ``DEFAULT_THROTTLE_CLASSES``; they read the fail-open cache."""
        throttle = _throttle(AnonRateThrottle, monkeypatch, _stopped_cache())

        assert throttle.allow_request(_request(), view=None) is True

    def test_the_throttle_key_really_raises_in_the_cache(self):
        with pytest.raises(core_caches.REDIS_UNAVAILABLE):
            _stopped_cache().get(
                throttling.GiftCardCheckThrottle.cache_format
                % {"scope": "gift_card_check", "ident": "1"},
                [],
            )

    def test_a_denial_is_logged_once_per_burst(self, monkeypatch, caplog):
        throttle = _throttle(
            throttling.CouponApplyThrottle, monkeypatch, _stopped_cache()
        )

        with caplog.at_level(logging.ERROR, logger="core.api.throttling"):
            for _ in range(5):
                throttle.allow_request(_request(), view=None)

        assert sum("DENIED" in r.getMessage() for r in caplog.records) == 1

    def test_staff_login_denies(self, monkeypatch):
        monkeypatch.setattr(
            throttling.StaffLoginThrottle, "cache", _stopped_cache()
        )
        monkeypatch.setattr(
            throttling.StaffLoginThrottle,
            "THROTTLE_RATES",
            {"staff_login": "10/hour"},
        )
        view = type("View", (), {"throttle_scope": "staff_login"})()

        throttle = throttling.StaffLoginThrottle()
        assert throttle.allow_request(_request(), view) is False

    @override_settings(ROOT_URLCONF=__name__)
    def test_a_view_answers_429_with_retry_after(self, monkeypatch):
        monkeypatch.setattr(
            throttling.GiftCardCheckThrottle, "cache", _stopped_cache()
        )
        monkeypatch.setattr(
            throttling.GiftCardCheckThrottle, "rate", "6/minute", raising=False
        )

        class _View(APIView):
            authentication_classes: list = []
            permission_classes: list = []
            throttle_classes = [throttling.GiftCardCheckThrottle]

            def get(self, request):
                return Response({"ok": True})

        response = _View.as_view()(
            APIRequestFactory().get("/x", REMOTE_ADDR="203.0.113.7")
        )

        assert response.status_code == 429
        assert int(response["Retry-After"]) >= 1


class TestRedisUp:
    def test_a_security_scope_throttles_normally(self, monkeypatch):
        cache = _live_cache()
        throttle_cls = throttling.GiftCardCheckThrottle
        monkeypatch.setattr(throttle_cls, "cache", cache)
        monkeypatch.setattr(throttle_cls, "rate", "2/minute", raising=False)
        request = _request()

        results = [
            throttle_cls().allow_request(request, None) for _ in range(3)
        ]

        assert results == [True, True, False]
        key = throttle_cls().get_cache_key(request, None)
        assert key.startswith(STRICT_NAMESPACE)
        cache.delete(key)

    def test_a_general_scope_throttles_normally(self, monkeypatch):
        cache = _live_cache()
        throttle_cls = throttling.SearchThrottle
        monkeypatch.setattr(throttle_cls, "cache", cache)
        monkeypatch.setattr(throttle_cls, "rate", "1/minute", raising=False)
        request = _request()

        results = [
            throttle_cls().allow_request(request, None) for _ in range(2)
        ]

        assert results == [True, False]
        cache.delete(throttle_cls().get_cache_key(request, None))


urlpatterns: list = []
