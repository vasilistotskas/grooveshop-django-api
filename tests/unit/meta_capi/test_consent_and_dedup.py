"""Regression tests for Meta CAPI Purchase dedup + registration consent.

- G0198: the COD Purchase fallback event_id must be deterministic per order
  so re-dispatches collide on Meta's dedup instead of double-counting.
- G0199: CompleteRegistration must not send the registrant's PII to Meta
  unless the signup request carries explicit ``ad_storage`` consent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import RequestFactory

from meta_capi.services import build_purchase_event
from meta_capi.signals import _on_user_signed_up
from order.factories.order import OrderFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestPurchaseEventDedup:
    def test_fallback_event_id_is_deterministic_per_order(self):
        order = OrderFactory(num_order_items=0)

        _, id1 = build_purchase_event(order)
        _, id2 = build_purchase_event(order)

        # No browser-minted id → deterministic per-order id, so two server
        # dispatches (e.g. COD + retry) dedup at Meta instead of counting
        # twice (G0198).
        assert id1 == f"purchase-{order.uuid}"
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
            "meta_capi.signals.dispatch_complete_registration_event.delay"
        ) as mock_delay:
            _on_user_signed_up(
                sender=None, request=self._request("granted"), user=user
            )
        mock_delay.assert_called_once()

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
