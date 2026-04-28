"""Unit tests for product-alert email unsubscribe headers.

Verifies that ``_send_product_alert_email`` emits Gmail/Yahoo-compliant
``List-Unsubscribe`` headers when ``user`` is provided and skips them
for guest alerts (no associated user → no token to mint).
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from product.tasks import _send_product_alert_email
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestProductAlertEmailUnsubscribeHeaders:
    """Cover both the user-bound and guest paths."""

    def _context(self) -> dict:
        return {
            "product_name": "Test Product",
            "product_url": "https://example.com/products/1/test",
            "SITE_NAME": "GrooveShop",
        }

    @override_settings(
        DEFAULT_FROM_EMAIL="from@example.com",
        INFO_EMAIL="info@example.com",
        SITE_NAME="GrooveShop",
        API_BASE_URL="https://api.example.com",
    )
    def test_user_bound_alert_emits_list_unsubscribe_headers(self):
        user = UserAccountFactory(num_addresses=0)
        mail.outbox = []

        sent = _send_product_alert_email(
            recipient=user.email,
            subject="Back in stock",
            context=self._context(),
            template_basename="restock_alert",
            user=user,
            list_id="product-restock-alerts",
        )

        assert sent is True
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]

        # RFC 8058: both URI forms separated by comma; both <>-bracketed.
        unsubscribe_header = msg.extra_headers["List-Unsubscribe"]
        assert "<mailto:info@example.com?subject=unsubscribe>" in unsubscribe_header
        assert "<https://api.example.com/api/v1/user/unsubscribe/" in unsubscribe_header

        # One-click POST flag — required by Gmail/Yahoo bulk-sender rules.
        assert (
            msg.extra_headers["List-Unsubscribe-Post"]
            == "List-Unsubscribe=One-Click"
        )

        # List-ID lets mailbox providers bucket per-list reputation.
        assert (
            msg.extra_headers["List-ID"]
            == "product-restock-alerts.GrooveShop"
        )

    @override_settings(DEFAULT_FROM_EMAIL="from@example.com")
    def test_guest_alert_omits_list_unsubscribe_headers(self):
        """No user → no token → no header (skipping is correct)."""
        mail.outbox = []

        sent = _send_product_alert_email(
            recipient="anon@example.com",
            subject="Back in stock",
            context=self._context(),
            template_basename="restock_alert",
            user=None,
            list_id="product-restock-alerts",
        )

        assert sent is True
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert "List-Unsubscribe" not in msg.extra_headers
        assert "List-Unsubscribe-Post" not in msg.extra_headers
        assert "List-ID" not in msg.extra_headers

    @override_settings(DEFAULT_FROM_EMAIL="from@example.com")
    def test_user_without_pk_falls_back_to_guest_path(self):
        """A defensive guard: an in-memory user that's never been
        saved has no pk and can't have a token minted; we must not
        crash and must not emit a malformed header."""
        mail.outbox = []
        sentinel_user = mock.Mock(pk=None)

        sent = _send_product_alert_email(
            recipient="user@example.com",
            subject="Back in stock",
            context=self._context(),
            template_basename="restock_alert",
            user=sentinel_user,
            list_id="product-restock-alerts",
        )

        assert sent is True
        msg = mail.outbox[0]
        assert "List-Unsubscribe" not in msg.extra_headers
