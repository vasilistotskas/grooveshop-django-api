"""What each throttle does while Redis is unreachable.

The default cache fails open, and DRF keeps throttle history with plain
``cache.get``/``cache.set``, so without this a Redis outage silently
lifted every rate limit — including the ones guarding a bearer secret
(gift cards), a code oracle (coupons), a third party (VIES) and money
(checkout). Security scopes now REFUSE: the cache's error reaches
``core.middleware.strict_cache``, which answers 503
``temporarily_unavailable`` (never a 429: nothing was sent too often);
general scopes ALLOW, as a storefront must keep serving through a Redis
blip. Every throttle is ours, keyed on the proven visitor.
"""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import path
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from core import caches as core_caches
from core.api import throttling
from core.caches import STRICT_NAMESPACE
from tests.utils.redis import private_cache, stopped_cache

#: The classification this module locks in. Changing a scope's policy
#: must be a deliberate edit here too.
FAIL_CLOSED = {
    "PaymentAttemptThrottle",
    "PaymentAttemptAnonThrottle",
    "OrderCreateThrottle",
    "OrderCreateAnonThrottle",
    "CouponApplyThrottle",
    "GiftCardCheckThrottle",
    "GiftCardCodeThrottle",
    "B2BProfileSubmitThrottle",
    "NewsletterSubscribeThrottle",
    "ProductAlertCreateThrottle",
    "ContactAttachmentThrottle",
    "VivaReturnThrottle",
    "AcsAddressValidationThrottle",
    "BoxNowNearestThrottle",
    "OrderSpecimenThrottle",
    "StaffLoginThrottle",
    "CartAgentTokenThrottle",
}
FAIL_OPEN = {
    "UserOrIpRateThrottle",
    "UserBudgetThrottle",
    "AnonIpRateThrottle",
    "GatewayAnonThrottle",
    "ContactCreateThrottle",
    "FeedbackCreateThrottle",
    "CartMutationThrottle",
    "CartMutationAnonThrottle",
    # The checkout quote is a read: a shopper keeps seeing a total
    # through a blip, as they keep their cart.
    "CartQuoteThrottle",
    "CartQuoteAnonThrottle",
    "SearchThrottle",
    "SearchClickThrottle",
    "ViewCountThrottle",
    "RecommendationEventThrottle",
    "EmailUnsubscribeThrottle",
    # Authenticated staff reads (agent/staff_views.py): no secret, oracle
    # or money behind it, and a store's team keeps working through a blip.
    "StaffAgentThrottle",
}


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
    def test_a_security_scope_refuses_and_logs_an_error(
        self, monkeypatch, caplog
    ):
        throttle = _throttle(
            throttling.GiftCardCheckThrottle, monkeypatch, stopped_cache()
        )

        with (
            caplog.at_level(logging.ERROR, logger="core.api.throttling"),
            pytest.raises(core_caches.REDIS_UNAVAILABLE),
        ):
            throttle.allow_request(_request(), view=None)

        assert any(
            "gift_card_check" in r.getMessage() and "DENIED" in r.getMessage()
            for r in caplog.records
        )

    def test_a_general_scope_allows_and_warns(self, monkeypatch, caplog):
        throttle = _throttle(
            throttling.SearchThrottle, monkeypatch, stopped_cache()
        )

        with caplog.at_level(logging.WARNING, logger="core.api.throttling"):
            assert throttle.allow_request(_request(), view=None) is True

        assert any("ALLOWED" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "name", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
    )
    def test_the_default_budgets_stay_open(self, monkeypatch, name):
        """``anon``/``user`` are the day-scale browsing ceilings in
        ``DEFAULT_THROTTLE_CLASSES``: ours, and failing open."""
        module, _, class_name = name.rpartition(".")
        assert module == throttling.__name__
        throttle = _throttle(
            getattr(throttling, class_name), monkeypatch, stopped_cache()
        )

        assert throttle.allow_request(_request(), view=None) is True

    def test_the_throttle_key_really_raises_in_the_cache(self):
        with pytest.raises(core_caches.REDIS_UNAVAILABLE):
            stopped_cache().get(
                throttling.GiftCardCheckThrottle.cache_format
                % {"scope": "gift_card_check", "ident": "1"},
                [],
            )

    def test_a_denial_is_logged_once_per_burst(self, monkeypatch, caplog):
        throttle = _throttle(
            throttling.CouponApplyThrottle, monkeypatch, stopped_cache()
        )

        with caplog.at_level(logging.ERROR, logger="core.api.throttling"):
            for _ in range(5):
                with pytest.raises(core_caches.REDIS_UNAVAILABLE):
                    throttle.allow_request(_request(), view=None)

        assert sum("DENIED" in r.getMessage() for r in caplog.records) == 1

    def test_staff_login_refuses(self, monkeypatch):
        monkeypatch.setattr(
            throttling.StaffLoginThrottle, "cache", stopped_cache()
        )
        monkeypatch.setattr(
            throttling.StaffLoginThrottle,
            "THROTTLE_RATES",
            {"staff_login": "10/hour"},
        )
        view = type("View", (), {"throttle_scope": "staff_login"})()

        throttle = throttling.StaffLoginThrottle()
        with pytest.raises(core_caches.REDIS_UNAVAILABLE):
            throttle.allow_request(_request(), view)

    @override_settings(ROOT_URLCONF=__name__)
    def test_a_view_answers_a_coded_503(self, monkeypatch):
        monkeypatch.setattr(
            throttling.GiftCardCheckThrottle, "cache", stopped_cache()
        )
        monkeypatch.setattr(
            throttling.GiftCardCheckThrottle, "rate", "6/minute", raising=False
        )

        response = Client().get("/throttled", REMOTE_ADDR="203.0.113.7")

        assert response.status_code == 503
        assert response.json()["code"] == "temporarily_unavailable"
        assert "Retry-After" not in response


class TestIdentity:
    @pytest.mark.parametrize(
        "throttle_class",
        [
            throttling.StaffLoginThrottle,
            throttling.PaymentAttemptThrottle,
            throttling.AnonIpRateThrottle,
            throttling.UserBudgetThrottle,
        ],
    )
    def test_an_anonymous_caller_is_the_proven_visitor(
        self, settings, throttle_class
    ):
        """Not DRF's ``get_ident`` (Traefik's peer, a Cloudflare edge node
        many visitors share): ``core.client_ip.client_ip_or_peer``."""
        settings.ORIGIN_VERIFY_SECRET = "edge-secret"
        request = APIRequestFactory().get(
            "/x",
            REMOTE_ADDR="10.42.1.0",
            HTTP_X_FORWARDED_FOR="203.0.113.50, 172.70.1.1",
            HTTP_X_ORIGIN_VERIFY="edge-secret",
            HTTP_CF_CONNECTING_IP="198.51.100.9",
        )

        assert throttle_class().get_ident(request) == "198.51.100.9"

    def test_no_module_uses_drfs_own_throttles(self):
        """DRF's classes key an anonymous caller on the peer; every
        throttle is defined in ``core.api.throttling``."""
        src = Path(throttling.__file__).resolve().parents[2]
        offenders = []
        for source in src.rglob("*.py"):
            if source == Path(throttling.__file__).resolve():
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            if any(_names_drf_throttling(node) for node in ast.walk(tree)):
                offenders.append(str(source.relative_to(src)))
        assert offenders == []


_DRF_THROTTLING = "rest_framework.throttling"


def _names_drf_throttling(node: ast.AST) -> bool:
    """Every way a module can reach DRF's throttles: ``import`` of the
    module, ``from rest_framework import throttling``, ``from
    rest_framework.throttling import ...``, or a dotted path in a string
    (``DEFAULT_THROTTLE_CLASSES``, ``throttle_classes`` set by name)."""
    if isinstance(node, ast.Import):
        return any(
            alias.name == _DRF_THROTTLING
            or alias.name.startswith(f"{_DRF_THROTTLING}.")
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        if node.module == _DRF_THROTTLING:
            return True
        return node.module == "rest_framework" and any(
            alias.name == "throttling" for alias in node.names
        )
    return isinstance(node, ast.Constant) and (
        isinstance(node.value, str) and _DRF_THROTTLING in node.value
    )


class TestRedisUp:
    def test_a_security_scope_throttles_normally(self, monkeypatch):
        cache = private_cache()
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
        cache = private_cache()
        throttle_cls = throttling.SearchThrottle
        monkeypatch.setattr(throttle_cls, "cache", cache)
        monkeypatch.setattr(throttle_cls, "rate", "1/minute", raising=False)
        request = _request()

        results = [
            throttle_cls().allow_request(request, None) for _ in range(2)
        ]

        assert results == [True, False]
        cache.delete(throttle_cls().get_cache_key(request, None))


class _GiftCardCheckView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [throttling.GiftCardCheckThrottle]

    def get(self, request):
        return Response({"ok": True})


urlpatterns = [path("throttled", _GiftCardCheckView.as_view())]
