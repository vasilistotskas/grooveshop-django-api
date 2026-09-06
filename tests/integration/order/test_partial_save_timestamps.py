"""``updated_at`` has to survive a partial save, or the grace window is a lie.

Django writes an ``auto_now`` column only when it appears in
``update_fields``. Every ``Order`` transition saves a narrow field list
(``["payment_status"]``, ``["status", "status_updated_at"]``, …), so
``updated_at`` kept whatever the last FULL save left — in practice the
creation timestamp, for the life of the order. Production order 264 ran
PENDING -> PROCESSING -> SHIPPED across two days and still reported
``updated_at == created_at``.

Two things depended on it:

* ``order/filters.py`` exposes ``updated_at`` as a client-facing filter
  (gte/lte/date), so "orders changed since X" omitted every transition.
* ``auto_cancel_stuck_pending_orders`` selects the FAILED bucket with
  ``updated_at__lt=now - ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES``,
  intending a retry grace measured from the payment failure. Measured
  from creation instead, an order whose payment failed later than the
  grace was cancellable on the very next run — stock released, customer
  emailed, while they were still re-entering their card.

The pre-existing suite could not catch this: its ``_create_stuck_order``
helper backdates the column with ``Order.objects.filter(...).update()``
and says outright that it is bypassing ``save()``. It proves the task's
FILTER works on a manufactured value; it never proves anything ever sets
that value. These tests drive the real transition instead.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.factories import PayWayFactory


@pytest.mark.django_db
class TestPartialSaveKeepsUpdatedAtHonest:
    def test_partial_save_bumps_updated_at(self):
        """The mechanism, in isolation."""
        order = OrderFactory(status=OrderStatus.PENDING)
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(hours=3)
        )
        stale = Order.objects.values_list("updated_at", flat=True).get(
            pk=order.pk
        )

        order.refresh_from_db()
        order.payment_status = PaymentStatus.FAILED
        order.save(update_fields=["payment_status"])

        fresh = Order.objects.values_list("updated_at", flat=True).get(
            pk=order.pk
        )
        assert fresh > stale, (
            "a narrow update_fields list must not suppress auto_now"
        )

    def test_status_change_persists_status_updated_at(self):
        """``save()`` assigns ``status_updated_at``; it must be written.

        A caller passing ``update_fields=["status"]`` would otherwise
        drop that assignment silently, while ``_original_status`` at the
        end of ``save()`` consumed the transition so it could never be
        written afterwards.
        """
        order = OrderFactory(status=OrderStatus.PENDING)
        Order.objects.filter(pk=order.pk).update(status_updated_at=None)
        order.refresh_from_db()

        order.status = OrderStatus.PROCESSING
        order.save(update_fields=["status"])

        persisted = Order.objects.values_list(
            "status_updated_at", flat=True
        ).get(pk=order.pk)
        assert persisted is not None


@pytest.mark.django_db
class TestFailedPaymentKeepsItsGraceWindow:
    """The bug that mattered, driven through the real service call."""

    def _order_placed_an_hour_ago(self):
        pay_way = PayWayFactory(is_online_payment=True, active=True)
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            pay_way=pay_way,
            payment_id="pi_grace_window_probe",
        )
        # The order has sat untouched for an hour. Both columns are
        # backdated because that is the real starting state - what is
        # under test is whether the SERVICE CALL below moves updated_at,
        # not whether a queryset update can.
        an_hour_ago = timezone.now() - timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(
            created_at=an_hour_ago, updated_at=an_hour_ago
        )
        order.refresh_from_db()
        return order

    def test_payment_failing_now_is_not_cancelled_immediately(self):
        """An hour-old order whose payment fails NOW keeps its window.

        With ``updated_at`` frozen at creation this order was an hour
        "stale" the instant it failed, so the very next run of the
        15-minute task cancelled it — zero grace.
        """
        order = self._order_placed_an_hour_ago()

        failed = OrderService.handle_payment_failed("pi_grace_window_probe")
        assert failed is not None, "sanity: the service found the order"

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        assert order.updated_at > order.created_at, (
            "the real transition must move updated_at, or the grace "
            "window is measured from checkout instead of from the failure"
        )

        auto_cancel_stuck_pending_orders()

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING, (
            "cancelled inside its retry grace window - the customer would "
            "return from re-entering their card to a dead order"
        )

    def test_still_cancelled_once_the_window_actually_elapses(self):
        """The other half: the task must not stop cancelling.

        Guards against 'fixing' the window by making it unreachable.
        """
        order = self._order_placed_an_hour_ago()
        OrderService.handle_payment_failed("pi_grace_window_probe")

        # Age the FAILED state past the grace window.
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )

        auto_cancel_stuck_pending_orders()

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
