"""Shopper-facing gift-card endpoints (``GiftCardViewSet``,
``giftcard/views.py``): balance check, purchase, purchase-status poll and
"my gift cards".

The merchant toggle (``GIFT_CARDS_ENABLED`` extra-setting) is patched
per test; the PSPs are the only other thing mocked — the provider's
tenant-credential check and ``order.payment.get_payment_provider``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import ANY, Mock, patch

import pytest
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from rest_framework.test import APIClient

from giftcard.enum import GiftCardPurchaseStatus, GiftCardStatus
from giftcard.factories import GiftCardFactory, GiftCardPurchaseFactory
from giftcard.models import GiftCardPurchase
from order.payment import StripePaymentProvider, VivaWalletPaymentProvider
from tenant.models import Tenant
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

CHECK_URL = reverse("giftcard:giftcard-check")
PURCHASE_URL = reverse("giftcard:giftcard-purchase")
STATUS_URL = reverse("giftcard:giftcard-purchase-status")
MINE_URL = reverse("giftcard:giftcard-mine")


@contextmanager
def _gift_cards(enabled: bool = True):
    values = {
        "GIFT_CARDS_ENABLED": enabled,
        "GIFT_CARD_MIN_AMOUNT": 10,
        "GIFT_CARD_MAX_AMOUNT": 500,
    }

    def _get(key, default=None):
        return values.get(key, default)

    with patch("giftcard.services.Setting.get", side_effect=_get):
        yield


@contextmanager
def _plan_without_gift_cards():
    tenant = Tenant(schema_name="acme", name="acme", gift_cards_enabled=False)
    with patch("tenant.membership.get_current_tenant", return_value=tenant):
        yield


def _client(user=None) -> APIClient:
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    if user is not None:
        client.force_authenticate(user=user)
    return client


class TestCheck:
    def test_known_code_returns_balance_case_insensitively(self):
        expires = timezone.now() + timedelta(days=30)
        card = GiftCardFactory(
            code="GC-ABCD-EFGH-JKLM",
            initial_value=Money(Decimal("50.00"), "EUR"),
            expires_at=expires,
        )

        with _gift_cards():
            response = _client().post(
                CHECK_URL, {"code": "  gc-abcd-efgh-jklm "}, format="json"
            )

        assert response.status_code == 200, response.data
        assert response.data["code"] == card.code
        assert response.data["balance"] == Decimal("50.00")
        assert response.data["currency"] == "EUR"
        assert response.data["is_redeemable"] is True
        assert response.data["expires_at"] is not None

    def test_disabled_card_is_found_but_not_redeemable(self):
        GiftCardFactory(
            code="GC-DEAD-DEAD-DEAD", status=GiftCardStatus.DISABLED
        )

        with _gift_cards():
            response = _client().post(
                CHECK_URL, {"code": "GC-DEAD-DEAD-DEAD"}, format="json"
            )

        assert response.status_code == 200, response.data
        assert response.data["is_redeemable"] is False
        assert response.data["balance"] == Decimal("50.00")

    def test_unknown_code_is_400_with_reason(self):
        with _gift_cards():
            response = _client().post(
                CHECK_URL, {"code": "GC-NOPE-NOPE-NOPE"}, format="json"
            )

        assert response.status_code == 400
        assert response.data == {
            "detail": "Unknown gift card code.",
            "reason": "gift_card_invalid",
        }

    def test_merchant_toggle_off_is_400_even_for_a_real_code(self):
        GiftCardFactory(code="GC-REAL-REAL-REAL")

        with _gift_cards(enabled=False):
            response = _client().post(
                CHECK_URL, {"code": "GC-REAL-REAL-REAL"}, format="json"
            )

        assert response.status_code == 400
        assert response.data == {
            "detail": "Gift cards are not available.",
            "reason": "gift_card_invalid",
        }

    def test_missing_code_is_a_field_error(self):
        with _gift_cards():
            response = _client().post(CHECK_URL, {}, format="json")

        assert response.status_code == 400
        assert "code" in response.data

    def test_plan_without_gift_cards_hides_the_endpoint(self):
        GiftCardFactory(code="GC-PLAN-PLAN-PLAN")

        with _gift_cards(), _plan_without_gift_cards():
            response = _client().post(
                CHECK_URL, {"code": "GC-PLAN-PLAN-PLAN"}, format="json"
            )

        assert response.status_code == 404


def _purchase_body(**overrides):
    body = {
        "amount": "25.00",
        "recipient_email": "friend@example.com",
        "recipient_name": "Friend",
        "sender_name": "Me",
        "message": "Enjoy",
    }
    body.update(overrides)
    return body


@contextmanager
def _stripe(provider: Mock):
    with (
        patch.object(
            StripePaymentProvider, "is_configured_for_tenant", return_value=True
        ),
        patch(
            "order.payment.get_payment_provider", return_value=provider
        ) as get_provider,
    ):
        yield get_provider


@contextmanager
def _viva(provider: Mock):
    with (
        patch.object(
            VivaWalletPaymentProvider,
            "is_configured_for_tenant",
            return_value=True,
        ),
        patch(
            "order.payment.get_payment_provider", return_value=provider
        ) as get_provider,
    ):
        yield get_provider


class TestPurchaseRefusals:
    def test_merchant_toggle_off_refuses_and_creates_nothing(self):
        with _gift_cards(enabled=False):
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(buyer_email="buyer@example.com"),
                format="json",
            )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Gift cards are not available.",
            "reason": "gift_card_invalid",
        }
        assert not GiftCardPurchase.objects.exists()

    def test_guest_without_buyer_email_is_refused(self):
        with _gift_cards():
            response = _client().post(
                PURCHASE_URL, _purchase_body(), format="json"
            )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Buyer email is required.",
            "reason": "gift_card_buyer_email_required",
        }
        assert not GiftCardPurchase.objects.exists()

    @pytest.mark.parametrize("amount", ["9.99", "500.01"])
    def test_amount_outside_the_configured_range_is_refused(self, amount):
        with _gift_cards():
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(amount=amount, buyer_email="b@example.com"),
                format="json",
            )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Gift card amount must be between 10 and 500 EUR.",
            "reason": "gift_card_invalid_amount",
        }
        assert not GiftCardPurchase.objects.exists()

    def test_provider_without_tenant_credentials_is_refused(self):
        provider = Mock()
        with (
            _gift_cards(),
            patch.object(
                StripePaymentProvider,
                "is_configured_for_tenant",
                return_value=False,
            ),
            patch("order.payment.get_payment_provider", return_value=provider),
        ):
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(buyer_email="b@example.com"),
                format="json",
            )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Gift card purchase is not available for this store.",
            "reason": "gift_card_purchase_unavailable",
        }
        provider.process_payment.assert_not_called()
        assert not GiftCardPurchase.objects.exists()

    def test_unregistered_payment_provider_is_a_field_error(self):
        with _gift_cards():
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(
                    buyer_email="b@example.com", payment_provider="paypal"
                ),
                format="json",
            )

        assert response.status_code == 400
        assert "payment_provider" in response.data
        assert not GiftCardPurchase.objects.exists()

    def test_missing_recipient_email_is_a_field_error(self):
        body = _purchase_body(buyer_email="b@example.com")
        del body["recipient_email"]

        with _gift_cards():
            response = _client().post(PURCHASE_URL, body, format="json")

        assert response.status_code == 400
        assert "recipient_email" in response.data
        assert not GiftCardPurchase.objects.exists()

    def test_provider_failure_marks_the_purchase_failed(self):
        provider = Mock()
        provider.process_payment.return_value = (False, {"error": "boom"})

        with _gift_cards(), _stripe(provider):
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(buyer_email="b@example.com"),
                format="json",
            )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Failed to start the payment.",
            "reason": "gift_card_payment_failed",
        }
        purchase = GiftCardPurchase.objects.get()
        assert purchase.status == GiftCardPurchaseStatus.FAILED
        assert purchase.payment_id == ""


class TestPurchaseStripe:
    def test_signed_in_buyer_gets_a_client_secret(self):
        user = UserAccountFactory()
        provider = Mock()
        provider.process_payment.return_value = (
            True,
            {"payment_id": "pi_gc_1", "client_secret": "pi_gc_1_secret"},
        )

        with _gift_cards(), _stripe(provider) as get_provider:
            response = _client(user).post(
                PURCHASE_URL, _purchase_body(), format="json"
            )

        assert response.status_code == 201, response.data
        purchase = GiftCardPurchase.objects.get()
        assert response.data == {
            "purchase_uuid": str(purchase.uuid),
            "provider": "stripe",
            "client_secret": "pi_gc_1_secret",
            "payment_intent_id": "pi_gc_1",
            "amount": Decimal("25.00"),
            "currency": "EUR",
        }
        get_provider.assert_called_once_with("stripe")
        provider.process_payment.assert_called_once_with(
            amount=Money(Decimal("25.00"), "EUR"),
            order_id=f"giftcard_{purchase.uuid}",
            order_uuid=f"giftcard_{purchase.uuid}",
            customer_email=user.email,
        )
        assert purchase.buyer == user
        assert purchase.buyer_email == user.email
        assert purchase.amount == Money(Decimal("25.00"), "EUR")
        assert purchase.recipient_email == "friend@example.com"
        assert purchase.recipient_name == "Friend"
        assert purchase.sender_name == "Me"
        assert purchase.message == "Enjoy"
        assert purchase.provider_code == "stripe"
        assert purchase.payment_id == "pi_gc_1"
        assert purchase.status == GiftCardPurchaseStatus.PENDING

    def test_guest_buyer_email_is_used_and_no_account_is_linked(self):
        provider = Mock()
        provider.process_payment.return_value = (
            True,
            {"payment_id": "pi_gc_2", "client_secret": "pi_gc_2_secret"},
        )

        with _gift_cards(), _stripe(provider):
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(buyer_email="guest@example.com"),
                format="json",
            )

        assert response.status_code == 201, response.data
        purchase = GiftCardPurchase.objects.get()
        assert purchase.buyer is None
        assert purchase.buyer_email == "guest@example.com"
        assert (
            provider.process_payment.call_args.kwargs["customer_email"]
            == "guest@example.com"
        )


class TestPurchaseViva:
    def test_hosted_provider_returns_a_checkout_url(self):
        provider = Mock()
        provider.create_checkout_session.return_value = (
            True,
            {
                "session_id": 7281946012,
                "checkout_url": "https://demo.vivapayments.com/web/checkout",
            },
        )

        with _gift_cards(), _viva(provider):
            response = _client().post(
                PURCHASE_URL,
                _purchase_body(
                    buyer_email="guest@example.com",
                    payment_provider="viva_wallet",
                ),
                format="json",
            )

        assert response.status_code == 201, response.data
        purchase = GiftCardPurchase.objects.get()
        assert response.data == {
            "purchase_uuid": str(purchase.uuid),
            "provider": "viva_wallet",
            "checkout_url": "https://demo.vivapayments.com/web/checkout",
            "amount": Decimal("25.00"),
            "currency": "EUR",
        }
        provider.create_checkout_session.assert_called_once_with(
            amount=Money(Decimal("25.00"), "EUR"),
            order_id=f"giftcard-{purchase.pk}",
            order_uuid=f"giftcard:{purchase.uuid}",
            description=ANY,
            customer_email="guest@example.com",
        )
        provider.process_payment.assert_not_called()
        # The webhook and the return resolver find the purchase by the
        # Smart Checkout order code.
        assert purchase.payment_id == "7281946012"
        assert purchase.provider_code == "viva_wallet"


class TestPurchaseStatus:
    def test_known_uuid_returns_the_status(self):
        purchase = GiftCardPurchaseFactory(status=GiftCardPurchaseStatus.PAID)

        with _gift_cards():
            response = _client().get(STATUS_URL, {"uuid": str(purchase.uuid)})

        assert response.status_code == 200, response.data
        assert response.data == {
            "purchase_uuid": str(purchase.uuid),
            "status": "PAID",
        }

    def test_missing_uuid_is_400(self):
        with _gift_cards():
            response = _client().get(STATUS_URL)

        assert response.status_code == 400
        assert response.json() == {"detail": "Missing uuid."}

    @pytest.mark.parametrize(
        "value", ["not-a-uuid", str(uuid.UUID(int=0))], ids=["bad", "unknown"]
    )
    def test_malformed_or_unknown_uuid_is_404(self, value):
        GiftCardPurchaseFactory()

        with _gift_cards():
            response = _client().get(STATUS_URL, {"uuid": value})

        assert response.status_code == 404
        assert response.json() == {"detail": "Purchase not found."}


class TestMine:
    def test_anonymous_caller_is_401(self):
        with _gift_cards():
            response = _client().get(MINE_URL)

        assert response.status_code == 401

    def test_lists_only_my_cards_newest_first(self):
        user = UserAccountFactory()
        older = GiftCardFactory(code="GC-MINE-0000-OLDR", issued_to=user)
        newer = GiftCardFactory(code="GC-MINE-0000-NEWR", issued_to=user)
        GiftCardFactory(
            code="GC-THEI-RS00-CARD", issued_to=UserAccountFactory()
        )
        GiftCardFactory(code="GC-NOBO-DY00-CARD")

        with _gift_cards():
            response = _client(user).get(MINE_URL)

        assert response.status_code == 200, response.data
        assert response.data["count"] == 2
        results = response.data["results"]
        assert [card["code"] for card in results] == [newer.code, older.code]
        assert results[0]["balance"] == Decimal("50.00")
        assert [t["kind"] for t in results[0]["transactions"]] == ["ISSUE"]

    def test_plan_without_gift_cards_is_404_for_a_signed_in_user(self):
        user = UserAccountFactory()

        with _gift_cards(), _plan_without_gift_cards():
            response = _client(user).get(MINE_URL)

        assert response.status_code == 404
