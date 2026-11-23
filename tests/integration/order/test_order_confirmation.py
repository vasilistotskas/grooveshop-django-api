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

from contextlib import ExitStack, contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.apps import apps
from django.db import transaction
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.lifecycle.confirmation import (
    CONFIRMATION_EMAIL_SENT_AT_KEY,
    backfill_confirmed_at,
)
from order.lifecycle.service import OrderLifecycleService
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payments import payment_hold
from order.payments.payment_ledger import PaymentLedger
from order.payments.stripe_settlement import settle_checkout_session
from order.payments.viva_settlement import (
    VIVA_TRANSACTION_PAYMENT_CREATED,
    apply_event,
    handle_payment_created,
    read_payment_created,
)
from order.services import OrderService
from order.signals import order_confirmed, order_created
from order.signals.handlers import announce_stripe_payment_applied
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.factories import PayWayFactory
from tests.utils.viva import verified_payment

pytestmark = [pytest.mark.django_db]

_VIVA_TRANSACTION = "e32827bb-03bf-43d1-abe9-593eb5d2cd43"

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


#: Django's own ``on_commit``, before the suite makes hooks run at once.
_ON_COMMIT = transaction.on_commit


@pytest.fixture
def publishers():
    """Each publisher's ``apply_async``."""
    with ExitStack() as stack:
        yield {
            name: stack.enter_context(patch(f"{path}.apply_async"))
            for name, path in _PUBLISHERS.items()
        }


@pytest.fixture
def commit(monkeypatch, django_capture_on_commit_callbacks):
    """Commit hooks that wait for the commit, as in production (the suite
    otherwise runs them at once); each ``with commit():`` block commits."""
    monkeypatch.setattr(transaction, "on_commit", _ON_COMMIT)
    return lambda: django_capture_on_commit_callbacks(execute=True)


@pytest.fixture
def published(publishers, django_capture_on_commit_callbacks):
    """Each publisher's ``apply_async``, with commit hooks run."""
    with django_capture_on_commit_callbacks(execute=True):
        yield publishers


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


class TestACanceledOrderIsNeverConfirmed:
    def test_confirm_refuses_a_canceled_order(self, published):
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.COMPLETED,
        )

        assert not OrderLifecycleService.confirm(Order.objects.get(pk=order.pk))
        order.refresh_from_db()
        assert order.confirmed_at is None
        assert _calls(published, "confirmation") == 0


@contextmanager
def _announcement_fails_once():
    """The first ``order_confirmed`` raises, as a broken receiver or a
    database error would, after the payment was committed."""
    failed = []

    def fail_once(sender, **kwargs):
        if not failed:
            failed.append(True)
            raise RuntimeError("announcement failed")

    order_confirmed.connect(fail_once, dispatch_uid="test.fail_once")
    try:
        yield
    finally:
        order_confirmed.disconnect(dispatch_uid="test.fail_once")


def _paid_but_unconfirmed(commit, provider: str, payment_id: str, **fields):
    """A card order, placed and committed, whose payment was then committed
    COMPLETED while its confirmation failed: the claim rolled back with the
    announcement."""
    with commit():
        order = _created(
            _order(PayWayFactory.create_online_payment(provider), **fields)
        )
    with commit():
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
            payment_id=payment_id,
        )
        with _announcement_fails_once(), pytest.raises(RuntimeError):
            OrderLifecycleService.confirm(Order.objects.get(pk=order.pk))
    order.refresh_from_db()
    assert order.confirmed_at is None
    return order


def _assert_told_once(published, order):
    order.refresh_from_db()
    assert order.confirmed_at is not None
    assert _calls(published, "confirmation") == 1
    assert _calls(published, "staff") == 1
    assert _calls(published, "paid_note") == 1
    assert _calls(published, "placed_note") == 0


class TestAPaidOrderLeftUnconfirmedHealsItself:
    """A payment committed COMPLETED whose confirmation failed is
    confirmed by the next path that sees it settled, even one that applies
    nothing (a repeat of the event, a poll that finds it paid already),
    and people are told once.

    Commit hooks wait for the commit here (``commit``), so the failed
    confirmation's rollback takes its announcement with it.
    """

    def test_confirm_refuses_a_card_order_not_paid(self, publishers, commit):
        with commit():
            order = _created(
                _order(PayWayFactory.create_online_payment("stripe"))
            )

            assert not OrderLifecycleService.confirm(order)

        order.refresh_from_db()
        assert order.confirmed_at is None
        assert _calls(publishers, "confirmation") == 0

    def test_the_stripe_webhook_that_applies_nothing_confirms_it(
        self, publishers, commit
    ):
        order = _paid_but_unconfirmed(commit, "stripe", "pi_heal")

        with commit():
            with patch(
                "shipping.services.ShippingService.dispatch_create_shipment_task"
            ):
                for _ in range(2):
                    outcome = OrderService.handle_payment_succeeded(
                        "pi_heal", captured=order.calculate_order_total_amount()
                    )
                    assert not outcome.applied
                    announce_stripe_payment_applied(outcome, "pi_heal")

        _assert_told_once(publishers, order)

    def test_a_poll_that_finds_it_paid_confirms_it(self, publishers, commit):
        order = _paid_but_unconfirmed(commit, "stripe", "pi_heal")

        with commit():
            provider = Mock()
            provider.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {"amount": "1", "currency": "EUR"},
            )

            with patch(
                "order.payments.payment.get_payment_provider",
                return_value=provider,
            ):
                for _ in range(2):
                    OrderService.get_payment_status(
                        Order.objects.get(pk=order.pk)
                    )

        _assert_told_once(publishers, order)

    def test_a_repeated_checkout_session_confirms_it(self, publishers, commit):
        order = _paid_but_unconfirmed(commit, "stripe", "pi_heal")

        with commit():
            for _ in range(2):
                settle_checkout_session(
                    order.pk,
                    session_id="cs_heal",
                    payment_intent_id="pi_heal",
                    captured=order.calculate_order_total_amount(),
                )

        _assert_told_once(publishers, order)

    def test_a_repeated_viva_payment_event_confirms_it(
        self, publishers, commit
    ):
        order = _paid_but_unconfirmed(
            commit,
            "viva_wallet",
            _VIVA_TRANSACTION,
            metadata={"viva_order_codes": ["4507133103272600"]},
        )

        with commit():
            VivaWebhookEvent.objects.create(
                transaction_id=_VIVA_TRANSACTION,
                event_type_id=VIVA_TRANSACTION_PAYMENT_CREATED,
                order=order,
                order_code="4507133103272600",
                status_id="F",
            )

            for _ in range(2):
                outcome = apply_event(
                    Order.objects.get(pk=order.pk),
                    event_type_id=VIVA_TRANSACTION_PAYMENT_CREATED,
                    event_data={"StatusId": "F"},
                    transaction_id=_VIVA_TRANSACTION,
                    order_code="4507133103272600",
                    status_id="F",
                    handle=handle_payment_created,
                    read=read_payment_created,
                )
                assert outcome is None

        _assert_told_once(publishers, order)


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


def _book_charge(order, *, at):
    """The ledger's CHARGE row for *order*'s payment, booked at *at*."""
    row, _ = PaymentLedger.record_charge(
        order,
        amount=order.calculate_order_total_amount(),
        provider="stripe",
        reference=f"pi_{order.pk}",
    )
    PaymentTransaction.objects.filter(pk=row.pk).update(created_at=at)


class TestTheBackfill:
    """``backfill_confirmed_at`` stamps only the orders the previous
    release announced: unconfirmed orders whose confirmation email went
    out (``metadata.confirmation_email_sent_at``). The new release sends
    that email only from a committed confirmation, so any other order is
    its to confirm, and stamping it would silence its announcement."""

    def test_it_stamps_only_what_the_previous_release_announced(self):
        offline_announced = _order(PayWayFactory.create_offline_payment())
        online_announced = _order(
            PayWayFactory.create_online_payment("stripe"),
            payment_status=PaymentStatus.COMPLETED,
        )
        offline_unannounced = _order(PayWayFactory.create_offline_payment())
        paid_unannounced = _order(
            PayWayFactory.create_online_payment("stripe"),
            payment_status=PaymentStatus.COMPLETED,
        )
        _book_charge(paid_unannounced, at=timezone.now())
        _order(PayWayFactory.create_online_payment("stripe"))
        # Placed under the previous release: nothing confirmed, and only
        # the announced orders' confirmation emails sent.
        Order.objects.update(confirmed_at=None, metadata={})
        Order.objects.filter(
            pk__in=[offline_announced.pk, online_announced.pk]
        ).update(
            metadata={
                CONFIRMATION_EMAIL_SENT_AT_KEY: timezone.now().isoformat()
            }
        )

        assert backfill_confirmed_at(apps) == 2
        assert backfill_confirmed_at(apps) == 0

        stamped = set(
            Order.objects.filter(confirmed_at__isnull=False).values_list(
                "pk", flat=True
            )
        )
        assert stamped == {offline_announced.pk, online_announced.pk}
        assert offline_unannounced.pk not in stamped

    def test_an_order_placed_now_is_left_to_its_own_confirmation(
        self, publishers, commit
    ):
        """CodeRabbit's sequence on #816: the new release commits an
        offline order, and the backfill runs before the commit hook that
        confirms it."""
        with commit():
            order = _order(PayWayFactory.create_offline_payment())

            assert backfill_confirmed_at(apps) == 0

        order.refresh_from_db()
        assert order.confirmed_at is not None
        assert _calls(publishers, "confirmation") == 1
        assert _calls(publishers, "staff") == 1
        assert _calls(publishers, "placed_note") == 1

    @pytest.mark.parametrize(
        "booked", [False, True], ids=["taken-at-once", "booked"]
    )
    def test_a_payment_the_new_release_recorded_is_still_announced(
        self, published, booked
    ):
        """CodeRabbit's sequences on #816: the new release places a card
        order and records its payment (taken at once, or booked by a
        webhook, its confirmation failing), and the backfill runs before
        the confirmation does."""
        order = _created(_order(PayWayFactory.create_online_payment("stripe")))
        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.COMPLETED, payment_id="pi_rollout"
        )
        if booked:
            _book_charge(order, at=timezone.now())

        assert backfill_confirmed_at(apps) == 0
        assert OrderLifecycleService.confirm(Order.objects.get(pk=order.pk))

        _assert_told_once(published, order)
