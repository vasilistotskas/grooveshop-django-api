"""``POST /api/v1/order/{id}/retry-payment`` (``OrderViewSet.retry_payment``,
``order/views/order.py``).

The view mints a fresh PaymentIntent for an unpaid order, swaps the new
intent id onto the order, resets ``payment_status`` to PENDING, re-arms
the confirmation / failure email flags and returns the client secret.
Only the PSP is mocked: ``PayWayService.get_provider_for_pay_way`` hands
back a stand-in provider, so ``PayWayService.process_payment`` and the
view's own bookkeeping run for real.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from pay_way.services import PayWayService
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

NEW_INTENT = {
    "payment_id": "pi_new_retry",
    "status": PaymentStatus.PENDING,
    "amount": "40.00",
    "currency": "EUR",
    "provider": "stripe",
    "client_secret": "pi_new_retry_secret_abc",
}


def _url(order: Order, uuid=None) -> str:
    url = reverse("order-retry-payment", kwargs={"pk": order.pk})
    return f"{url}?uuid={uuid}" if uuid else url


def _client(user=None) -> APIClient:
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _stripe_order(**kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("payment_status", PaymentStatus.FAILED)
    kwargs.setdefault("payment_id", "pi_old_failed")
    order = OrderFactory(num_order_items=0, **kwargs)
    order.items.create(
        product=ProductFactory(stock=10, active=True),
        price=Money(Decimal("20.00"), "EUR"),
        quantity=2,
    )
    return order


def _provider(result=(True, NEW_INTENT)) -> Mock:
    provider = Mock()
    provider.process_payment.return_value = result
    return provider


def _patch_provider(provider: Mock):
    return patch.object(
        PayWayService, "get_provider_for_pay_way", return_value=provider
    )


class TestRetryPaymentSuccess:
    def test_swaps_intent_resets_status_and_returns_client_secret(self):
        user = UserAccountFactory()
        order = _stripe_order(
            user=user,
            metadata={
                "confirmation_email_sent_at": "2026-09-01T10:00:00+00:00",
                "payment_failed_email_sent": True,
            },
        )
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(
                _url(order),
                {"payment_method_id": "pm_card_visa"},
                format="json",
            )

        assert response.status_code == 200, response.data
        assert response.data == {
            "payment_id": "pi_new_retry",
            "status": "PENDING",
            "amount": "40.00",
            "currency": "EUR",
            "provider": "stripe",
            "client_secret": "pi_new_retry_secret_abc",
            "requires_action": False,
        }
        order.refresh_from_db()
        provider.process_payment.assert_called_once_with(
            amount=order.calculate_order_total_amount(),
            order_id=str(order.id),
            payment_method_id="pm_card_visa",
        )
        assert order.payment_id == "pi_new_retry"
        assert order.payment_status == PaymentStatus.PENDING
        assert "confirmation_email_sent_at" not in order.metadata
        assert "payment_failed_email_sent" not in order.metadata
        [retry] = order.metadata["payment_retries"]
        assert retry["previous_payment_id"] == "pi_old_failed"
        assert retry["new_payment_id"] == "pi_new_retry"

    def test_logs_the_swap_in_order_history(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user)

        with _patch_provider(_provider()):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 200, response.data
        entry = OrderHistory.objects.get(order=order, change_type="PAYMENT")
        assert entry.previous_value == {
            "payment_status": "FAILED",
            "payment_id": "pi_old_failed",
        }
        assert entry.new_value == {
            "payment_status": PaymentStatus.PENDING.value,
            "payment_id": "pi_new_retry",
            "retry": True,
        }

    def test_a_canceled_intent_on_a_pending_order_may_retry(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user, payment_status=PaymentStatus.CANCELED)

        with _patch_provider(_provider()):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 200, response.data
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PENDING
        assert order.payment_id == "pi_new_retry"

    def test_a_pending_order_may_retry(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user, payment_status=PaymentStatus.PENDING)

        with _patch_provider(_provider()):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 200, response.data
        order.refresh_from_db()
        assert order.payment_id == "pi_new_retry"

    def test_guest_with_the_order_uuid_may_retry(self):
        order = _stripe_order(user=None, email="guest@example.com")

        with _patch_provider(_provider()):
            response = _client().post(
                _url(order, uuid=order.uuid), {}, format="json"
            )

        assert response.status_code == 200, response.data
        assert response.data["client_secret"] == "pi_new_retry_secret_abc"
        order.refresh_from_db()
        assert order.payment_id == "pi_new_retry"


class TestRetryPaymentRefusals:
    def test_already_paid_order_is_refused(self):
        user = UserAccountFactory()
        order = _stripe_order(
            user=user,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_paid",
        )
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {"detail": "This order has already been paid."}
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.payment_id == "pi_paid"
        assert order.payment_status == PaymentStatus.COMPLETED

    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.PROCESSING,
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        ],
    )
    def test_non_retryable_payment_status_is_refused(self, payment_status):
        user = UserAccountFactory()
        order = _stripe_order(user=user, payment_status=payment_status)
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "This order's payment status does not allow a retry."
        }
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == payment_status
        assert order.payment_id == "pi_old_failed"

    def test_canceled_order_is_refused(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user, payment_status=PaymentStatus.PENDING)
        OrderService.cancel_order(order, refund_payment=False)
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.CANCELED
        metadata_before = order.metadata
        history_before = OrderHistory.objects.filter(order=order).count()
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "This order is no longer open for payment."
        }
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.CANCELED
        assert order.payment_id == "pi_old_failed"
        assert order.metadata == metadata_before
        assert (
            OrderHistory.objects.filter(order=order).count() == history_before
        )

    @pytest.mark.parametrize(
        "order_status",
        [
            OrderStatus.PROCESSING,
            OrderStatus.RETURNED,
            OrderStatus.COMPLETED,
        ],
    )
    def test_order_past_pending_is_refused(self, order_status):
        user = UserAccountFactory()
        order = _stripe_order(user=user)
        Order.objects.filter(pk=order.pk).update(status=order_status)
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "This order is no longer open for payment."
        }
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        assert order.payment_id == "pi_old_failed"

    @pytest.mark.parametrize(
        "make_pay_way",
        [
            lambda: PayWayFactory.create_online_payment(
                provider_code="viva_wallet"
            ),
            lambda: PayWayFactory.create_offline_payment(
                provider_code="cash_on_delivery", requires_confirmation=False
            ),
        ],
        ids=["hosted_viva", "cash_on_delivery"],
    )
    def test_pay_way_without_payment_intents_is_refused(self, make_pay_way):
        user = UserAccountFactory()
        order = _stripe_order(user=user, pay_way=make_pay_way())
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "Retry is only supported for Stripe payments."
        }
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.payment_id == "pi_old_failed"

    def test_order_without_pay_way_is_refused(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user)
        Order.objects.filter(pk=order.pk).update(pay_way=None)

        response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "Retry is only supported for Stripe payments."
        }

    def test_provider_failure_returns_400_and_leaves_order_untouched(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user)
        provider = _provider(result=(False, {"error": "card_declined"}))

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 400
        assert response.data == {
            "detail": "Failed to create payment intent.",
            "error": {"error": "card_declined"},
        }
        order.refresh_from_db()
        assert order.payment_id == "pi_old_failed"
        assert order.payment_status == PaymentStatus.FAILED
        assert "payment_retries" not in (order.metadata or {})
        assert not OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).exists()

    def test_reserved_payment_data_key_is_a_validation_error(self):
        user = UserAccountFactory()
        order = _stripe_order(user=user)
        provider = _provider()

        with _patch_provider(provider):
            response = _client(user).post(
                _url(order),
                {"payment_data": {"amount": "1"}},
                format="json",
            )

        assert response.status_code == 400
        assert "payment_data" in response.data
        provider.process_payment.assert_not_called()


class TestRetryPaymentConcurrentChange:
    """The provider call runs without the row lock. Whatever landed on
    the order meanwhile wins: the retry is refused, and the new intent's
    client secret is never handed out, so it can never be charged."""

    @pytest.mark.parametrize(
        "meanwhile",
        [
            pytest.param(
                {
                    "status": OrderStatus.CANCELED,
                    "payment_status": PaymentStatus.CANCELED,
                },
                id="customer-canceled",
            ),
            pytest.param(
                {"payment_status": PaymentStatus.COMPLETED},
                id="old-intent-succeeded",
            ),
        ],
    )
    def test_a_change_during_the_provider_call_wins(self, meanwhile):
        user = UserAccountFactory()
        order = _stripe_order(user=user)
        provider = _provider()

        def change_then_mint(**kwargs):
            Order.objects.filter(pk=order.pk).update(**meanwhile)
            return (True, NEW_INTENT)

        provider.process_payment.side_effect = change_then_mint

        with _patch_provider(provider):
            response = _client(user).post(_url(order), {}, format="json")

        assert response.status_code == 409, response.data
        assert response.data == {
            "detail": "This order changed while the payment was being "
            "prepared. Reload it and try again."
        }
        order.refresh_from_db()
        for field, value in meanwhile.items():
            assert getattr(order, field) == value
        assert order.payment_id == "pi_old_failed"
        assert "payment_retries" not in (order.metadata or {})
        assert not OrderHistory.objects.filter(
            order=order, new_value__retry=True
        ).exists()


class TestRetryPaymentPermissions:
    def test_another_users_order_is_forbidden(self):
        owner = UserAccountFactory()
        order = _stripe_order(user=owner)
        provider = _provider()

        with _patch_provider(provider):
            response = _client(UserAccountFactory()).post(
                _url(order), {}, format="json"
            )

        assert response.status_code == 403
        provider.process_payment.assert_not_called()
        order.refresh_from_db()
        assert order.payment_id == "pi_old_failed"

    def test_anonymous_caller_cannot_retry_a_users_order(self):
        order = _stripe_order(user=UserAccountFactory())
        provider = _provider()

        with _patch_provider(provider):
            response = _client().post(
                _url(order, uuid=order.uuid), {}, format="json"
            )

        assert response.status_code == 403
        provider.process_payment.assert_not_called()

    def test_guest_order_without_uuid_is_forbidden(self):
        order = _stripe_order(user=None, email="guest@example.com")
        provider = _provider()

        with _patch_provider(provider):
            response = _client().post(_url(order), {}, format="json")

        assert response.status_code == 403
        provider.process_payment.assert_not_called()

    def test_guest_order_with_wrong_uuid_is_forbidden(self):
        order = _stripe_order(user=None, email="guest@example.com")
        other = _stripe_order(user=None, email="other@example.com")
        provider = _provider()

        with _patch_provider(provider):
            response = _client().post(
                _url(order, uuid=other.uuid), {}, format="json"
            )

        assert response.status_code == 403
        provider.process_payment.assert_not_called()

    def test_unknown_order_is_404(self):
        user = UserAccountFactory()

        response = _client(user).post(
            reverse("order-retry-payment", kwargs={"pk": 987654321}),
            {},
            format="json",
        )

        assert response.status_code == 404
