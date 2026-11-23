"""An order is confirmed once: at creation when nothing is left to pay
online, or when its online card payment settles.

Confirming (``OrderLifecycleService.confirm``) is what tells people about
an order: the customer's confirmation email, the staff "New order" email
and one in-app note. It claims ``Order.confirmed_at`` with a single
``UPDATE ... WHERE confirmed_at IS NULL``, so however many paths report the
same payment (the webhook, the buyer's return, the sweep's read-back, a
redelivery), people hear about the order once. A card order nobody paid
is never confirmed, and its cancel is silent to the customer.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.lifecycle.service import OrderLifecycleService
from order.models.order import Order
from order.payments import payment_hold
from order.payments.viva_settlement import handle_payment_created
from order.signals import order_created
from order.signals.handlers import announce_stripe_payment_applied
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.factories import PayWayFactory
from tests.utils.viva import verified_payment

pytestmark = [pytest.mark.django_db]

#: Every task that tells someone about an order, by what it is.
_PUBLISHERS = {
    "confirmation": "order.tasks.send_order_confirmation_email",
    "staff": "order.tasks.send_admin_new_order_email",
    "placed_note": "order.communications.notifications.notify_order_created_live",
    "paid_note": (
        "order.communications.notifications.notify_payment_confirmed_live"
    ),
    "attach": "recommendation.tasks.record_recommendation_attach",
    "status_email": "order.tasks.send_order_status_update_email",
    "status_note": (
        "order.communications.notifications.notify_order_status_changed_live"
    ),
}


@pytest.fixture
def published(django_capture_on_commit_callbacks):
    """Each publisher's ``apply_async``, with commit hooks run."""
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch(f"{path}.apply_async"))
            for name, path in _PUBLISHERS.items()
        }
        stack.enter_context(django_capture_on_commit_callbacks(execute=True))
        yield mocks


def _calls(published, name: str) -> int:
    return published[name].call_count


def _order(pay_way, **fields):
    fields.setdefault("status", OrderStatus.PENDING)
    fields.setdefault("payment_status", PaymentStatus.PENDING)
    return OrderFactory(num_order_items=1, pay_way=pay_way, **fields)


def _created(order):
    order_created.send(sender=Order, order=order)
    order.refresh_from_db()
    return order


class TestConfirmedAtCreation:
    @pytest.mark.parametrize(
        "pay_way",
        [
            lambda: PayWayFactory.create_offline_payment(
                settlement="courier_cash"
            ),
            lambda: PayWayFactory.create_carrier_terminal_payment(),
            lambda: PayWayFactory.create_offline_payment(),
        ],
        ids=["cash-on-delivery", "box-now-pay-on-the-go", "bank-transfer"],
    )
    def test_an_order_paid_later_offline_is_confirmed_at_once(
        self, published, pay_way
    ):
        order = _created(_order(pay_way()))

        assert order.confirmed_at is not None
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "placed_note") == 1
        assert _calls(published, "paid_note") == 0
        assert _calls(published, "attach") == 1

    def test_an_online_order_paid_at_creation_is_confirmed_at_once(
        self, published
    ):
        order = _created(
            _order(
                PayWayFactory.create_online_payment("stripe"),
                payment_status=PaymentStatus.COMPLETED,
            )
        )

        assert order.confirmed_at is not None
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "paid_note") == 1
        assert _calls(published, "placed_note") == 0

    @pytest.mark.parametrize("provider", ["stripe", "viva_wallet"])
    def test_an_unpaid_card_order_tells_nobody(self, published, provider):
        order = _created(_order(PayWayFactory.create_online_payment(provider)))

        assert order.confirmed_at is None
        for name in ("confirmation", "staff", "placed_note", "paid_note"):
            assert _calls(published, name) == 0, name
        assert _calls(published, "attach") == 0
        assert order.history.filter(
            change_type="NOTE", new_value={"note": "Order created"}
        ).exists()


class TestConfirmedWhenThePaymentSettles:
    def test_confirming_twice_tells_people_once(self, published):
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.COMPLETED
        )
        order.refresh_from_db()

        first = OrderLifecycleService.confirm(order)
        second = OrderLifecycleService.confirm(order)
        third = OrderLifecycleService.confirm(Order.objects.get(pk=order.pk))

        assert (first, second, third) == (True, False, False)
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "paid_note") == 1
        assert _calls(published, "attach") == 1

    def test_a_viva_payment_confirms_the_order_once(self, published):
        order = _created(
            _order(
                PayWayFactory.create_online_payment("viva_wallet"),
                metadata={"viva_order_codes": ["4507133103272600"]},
            )
        )
        verified = (
            PaymentStatus.COMPLETED,
            verified_payment(order, "4507133103272600"),
        )

        with patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ):
            for _ in range(3):
                handle_payment_created(
                    Order.objects.get(pk=order.pk),
                    {"StatusId": "F"},
                    "e32827bb-03bf-43d1-abe9-593eb5d2cd43",
                    verified=verified,
                )

        order.refresh_from_db()
        assert order.confirmed_at is not None
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "paid_note") == 1
        assert _calls(published, "placed_note") == 0

    def test_a_stripe_payment_confirms_the_order_once(self, published):
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.COMPLETED, payment_id="pi_confirm"
        )

        class _Applied:
            applied = True
            previous_payment_status = PaymentStatus.PENDING

        for _ in range(2):
            outcome = _Applied()
            outcome.order = Order.objects.get(pk=order.pk)
            announce_stripe_payment_applied(outcome, "pi_confirm")

        order.refresh_from_db()
        assert order.confirmed_at is not None
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "paid_note") == 1

    def test_a_failed_payment_tells_nobody_and_records_it(self, published):
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(payment_id="pi_declined")

        from order.services import OrderService

        outcome = OrderService.handle_payment_failed("pi_declined")

        assert outcome.applied
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        assert order.confirmed_at is None
        for name in ("confirmation", "staff", "placed_note", "paid_note"):
            assert _calls(published, name) == 0, name

    def test_a_payment_that_succeeds_after_a_failure_confirms_once(
        self, published
    ):
        """The shopper's first card is declined, the second goes through:
        the failure told no one, the success confirms the order once."""
        from order.services import OrderService

        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(payment_id="pi_retry")
        OrderService.handle_payment_failed("pi_retry")
        assert _calls(published, "confirmation") == 0

        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.COMPLETED
        )

        class _Applied:
            applied = True
            previous_payment_status = PaymentStatus.FAILED

        outcome = _Applied()
        outcome.order = Order.objects.get(pk=order.pk)
        announce_stripe_payment_applied(outcome, "pi_retry")

        order.refresh_from_db()
        assert order.confirmed_at is not None
        assert _calls(published, "confirmation") == 1
        assert _calls(published, "staff") == 1
        assert _calls(published, "paid_note") == 1


class TestCancelOfAnUnconfirmedOrder:
    def test_the_sweep_cancels_an_unpaid_card_order_silently(self, published):
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(
            payment_hold_expires_at=timezone.now() - timedelta(hours=2)
        )

        result = auto_cancel_stuck_pending_orders()

        assert result["canceled_pending"] == 1
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert _calls(published, "status_email") == 0
        assert _calls(published, "status_note") == 0
        assert not payment_hold.awaiting_payment(order)

    def test_a_confirmed_order_canceled_still_tells_the_customer(
        self, published
    ):
        order = _created(_order(PayWayFactory.create_offline_payment()))

        OrderLifecycleService.cancel_order(
            order,
            reason="Customer asked",
            refund_payment=False,
            customer_initiated=False,
        )

        assert _calls(published, "status_email") == 1
