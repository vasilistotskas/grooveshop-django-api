"""Regression tests for Meta CAPI Purchase dedup + registration consent.

- G0198: the COD Purchase fallback event_id must be deterministic per order
  so re-dispatches collide on Meta's dedup instead of double-counting.
- G0199: CompleteRegistration must not send the registrant's PII to Meta
  unless the signup request carries explicit ``ad_storage`` consent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django.test import RequestFactory

from meta_capi.services import build_purchase_event
from meta_capi.signals import _on_user_signed_up
from meta_capi.tasks import _serialize_event_payload
from order.factories.order import OrderFactory
from user.factories.account import UserAccountFactory


class _FakeUserData:
    def normalize(self):
        # Meta hashes em/ph but sends IP/UA/fbp/fbc in the clear.
        return {
            "em": "hashed-email",
            "client_ip_address": "203.0.113.9",
            "client_user_agent": "Mozilla/5.0",
            "fbp": "fb.1.123",
            "fbc": "fb.1.456",
        }


class _FakeEvent:
    def normalize(self):
        return {
            "event_name": "Purchase",
            "user_data": _FakeUserData().normalize(),
        }


def test_audit_payload_strips_unhashed_pii():
    """The persisted audit payload must not retain the raw client IP / user
    agent / _fbp / _fbc — CAPI does not hash those (G0200)."""
    payload = _serialize_event_payload(_FakeEvent())

    user_data = payload["user_data"]
    assert "em" in user_data  # hashed identifier retained
    for stripped in (
        "client_ip_address",
        "client_user_agent",
        "fbp",
        "fbc",
    ):
        assert stripped not in user_data


@pytest.mark.django_db
class TestPurchaseEventDedup:
    def test_fallback_event_id_is_deterministic_per_order(self):
        order = OrderFactory(num_order_items=0)

        _, id1 = build_purchase_event(order)
        _, id2 = build_purchase_event(order)

        # No browser-minted id → deterministic per-order id, so two server
        # dispatches (e.g. COD + retry) dedup at Meta instead of counting
        # twice (G0198).
        assert id1 == f"purchase-{order.pk}"
        assert str(order.uuid) not in id1
        assert id1 == id2


@pytest.mark.django_db
class TestCompleteRegistrationConsent:
    def _request(self, ad_storage=None):
        req = RequestFactory().post("/signup")
        req.COOKIES = {}
        if ad_storage is not None:
            req.COOKIES["ad_storage"] = ad_storage
        return req

    def test_dispatches_when_consent_granted(self):
        user = UserAccountFactory()
        with patch(
            "meta_capi.signals.dispatch_complete_registration_event.apply_async"
        ) as mock_dispatch:
            _on_user_signed_up(
                sender=None, request=self._request("granted"), user=user
            )
        # `apply_async` carrying the schema header, not a bare `delay`:
        # allauth's signup runs inside an atomic block, so the hook fires
        # after that block's schema context can unwind.
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.kwargs["headers"] == {
            "_schema_name": connection.schema_name
        }

    def test_no_dispatch_when_consent_denied_or_absent(self):
        user = UserAccountFactory()
        with patch(
            "meta_capi.signals.dispatch_complete_registration_event.delay"
        ) as mock_delay:
            _on_user_signed_up(
                sender=None, request=self._request("denied"), user=user
            )
            _on_user_signed_up(
                sender=None, request=self._request(None), user=user
            )
        mock_delay.assert_not_called()

    def test_no_dispatch_without_request(self):
        user = UserAccountFactory()
        with patch(
            "meta_capi.signals.dispatch_complete_registration_event.delay"
        ) as mock_delay:
            _on_user_signed_up(sender=None, request=None, user=user)
        mock_delay.assert_not_called()


@pytest.mark.django_db
class TestCompleteRegistrationClientIp:
    """The IP sent to Meta is the visitor our edge vouched for, or none.

    ``X-Real-IP`` on the SSR path is the caller's own ``CF-Connecting-IP``
    relayed by Nuxt, so without ``X-Origin-Verify`` it is caller-chosen
    (#638). The peer is not sent either: it is a proxy, not the browser
    Meta asks for.
    """

    SECRET = "s3cret-edge-token"
    VISITOR = "203.0.113.9"
    FORGED = "198.51.100.66"

    def _sent_ip(self, **meta):
        req = RequestFactory().post("/signup", REMOTE_ADDR="10.42.1.0", **meta)
        req.COOKIES = {"ad_storage": "granted"}
        with patch(
            "meta_capi.signals.dispatch_complete_registration_event.apply_async"
        ) as mock_dispatch:
            _on_user_signed_up(
                sender=None, request=req, user=UserAccountFactory()
            )
        mock_dispatch.assert_called_once()
        return mock_dispatch.call_args.kwargs["kwargs"]["client_ip_address"]

    def test_forged_x_real_ip_without_the_edge_proof_is_not_sent(
        self, settings
    ):
        settings.ORIGIN_VERIFY_SECRET = self.SECRET
        assert (
            self._sent_ip(
                HTTP_X_REAL_IP=self.FORGED,
                HTTP_CF_CONNECTING_IP=self.FORGED,
                HTTP_X_FORWARDED_FOR=f"{self.FORGED}, 162.158.1.1",
            )
            is None
        )

    def test_forged_x_real_ip_with_a_wrong_proof_is_not_sent(self, settings):
        settings.ORIGIN_VERIFY_SECRET = self.SECRET
        assert (
            self._sent_ip(
                HTTP_X_ORIGIN_VERIFY="not-the-secret",
                HTTP_X_REAL_IP=self.FORGED,
            )
            is None
        )

    def test_the_proxy_peer_is_not_sent_as_the_visitor(self, settings):
        settings.ORIGIN_VERIFY_SECRET = self.SECRET
        assert self._sent_ip(HTTP_X_FORWARDED_FOR="162.158.1.1") is None

    def test_the_edge_proven_visitor_is_sent(self, settings):
        settings.ORIGIN_VERIFY_SECRET = self.SECRET
        assert (
            self._sent_ip(
                HTTP_X_ORIGIN_VERIFY=self.SECRET,
                HTTP_X_REAL_IP=self.VISITOR,
            )
            == self.VISITOR
        )
