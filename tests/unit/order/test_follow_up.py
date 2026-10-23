"""The follow-up flag: one queryset rule, read by badge, filter and counter.

An order needs follow-up when its shopper has waited ``ORDER_FOLLOW_UP_DAYS``
calendar days (store timezone) and the order is still PENDING, PROCESSING or
SHIPPED, unless it is an online order nobody paid for. ``now`` is passed in
everywhere, so no test depends on the wall clock.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone, translation
from extra_settings.models import Setting

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.follow_up import (
    FOLLOW_UP_DAYS_SETTING,
    follow_up_reason,
    last_carrier_event_at,
)
from order.models.order import Order
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from shipping_acs.factories import AcsShipmentFactory
from tests.utils.orders import courier_cash_order

pytestmark = pytest.mark.django_db

ATHENS = ZoneInfo("Europe/Athens")

#: Saturday 17 October 2026, mid-morning in Athens (UTC+3 until 25 Oct).
NOW = datetime(2026, 10, 17, 10, 0, tzinfo=ATHENS)


@pytest.fixture(autouse=True)
def _store_time():
    with timezone.override(ATHENS):
        yield


def _days_setting(days: int) -> None:
    Setting.objects.update_or_create(
        name=FOLLOW_UP_DAYS_SETTING,
        defaults={"value_type": "int", "value_int": days},
    )


def _placed(created_at: datetime, **fields) -> Order:
    """A cash-on-delivery order placed at ``created_at``."""
    fields.setdefault("status", OrderStatus.PROCESSING)
    order = courier_cash_order(**fields)
    # created_at is auto_now_add: backdate through the queryset.
    Order.objects.filter(pk=order.pk).update(created_at=created_at)
    return order


def _flagged(now: datetime = NOW) -> set[int]:
    return set(
        Order.objects.needing_follow_up(now).values_list("pk", flat=True)
    )


class TestCalendarDays:
    """An order placed on local day D is flagged from 00:00 on D+N."""

    def test_flagged_from_the_start_of_day_d_plus_n(self):
        _days_setting(7)
        late_on_d = _placed(datetime(2026, 10, 10, 23, 59, tzinfo=ATHENS))
        early_on_d = _placed(datetime(2026, 10, 10, 0, 0, tzinfo=ATHENS))

        assert _flagged() == {late_on_d.pk, early_on_d.pk}

    def test_not_flagged_on_day_d_plus_n_minus_one(self):
        _days_setting(7)
        _placed(datetime(2026, 10, 11, 0, 0, tzinfo=ATHENS))
        _placed(datetime(2026, 10, 16, 23, 0, tzinfo=ATHENS))

        assert _flagged() == set()

    def test_the_flag_turns_on_at_midnight_not_after_24_hours(self):
        _days_setting(7)
        order = _placed(datetime(2026, 10, 10, 23, 59, tzinfo=ATHENS))

        before_midnight = datetime(2026, 10, 17, 23, 59, tzinfo=ATHENS)
        assert order.pk in _flagged(before_midnight)
        # The same order one calendar day earlier: D+6, still quiet.
        assert _flagged(datetime(2026, 10, 16, 23, 59, tzinfo=ATHENS)) == set()
        assert order.pk in _flagged(datetime(2026, 10, 17, 0, 0, tzinfo=ATHENS))

    def test_days_are_store_days_not_utc_days(self):
        """00:30 on 11 Oct in Athens is still 10 Oct in UTC; the store's
        day wins, so the order is on day D=11 and quiet at D+6."""
        _days_setting(7)
        _placed(datetime(2026, 10, 10, 21, 30, tzinfo=ZoneInfo("UTC")))

        assert _flagged() == set()
        assert len(_flagged(datetime(2026, 10, 18, 0, 0, tzinfo=ATHENS))) == 1

    def test_the_threshold_is_the_store_setting(self):
        order = _placed(datetime(2026, 10, 14, 12, 0, tzinfo=ATHENS))

        _days_setting(3)
        assert _flagged() == {order.pk}
        _days_setting(4)
        assert _flagged() == set()

    def test_unset_falls_back_to_the_default(self):
        Setting.objects.filter(name=FOLLOW_UP_DAYS_SETTING).delete()
        order = _placed(datetime(2026, 10, 10, 12, 0, tzinfo=ATHENS))

        assert _flagged() == {order.pk}

    def test_zero_switches_the_flag_off(self):
        _days_setting(0)
        _placed(datetime(2020, 1, 1, 12, 0, tzinfo=ATHENS))

        assert _flagged() == set()
        assert (
            not Order.objects.with_follow_up(NOW)
            .filter(needs_follow_up=True)
            .exists()
        )


class TestStatuses:
    OLD = datetime(2026, 9, 1, 12, 0, tzinfo=ATHENS)

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.SHIPPED],
    )
    def test_a_parcel_the_shopper_still_waits_for_is_flagged(self, status):
        _days_setting(7)
        order = _placed(self.OLD, status=status)

        assert _flagged() == {order.pk}

    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.RETURNED,
            OrderStatus.CANCELED,
            OrderStatus.REFUNDED,
        ],
    )
    def test_a_finished_order_is_not(self, status):
        _days_setting(7)
        _placed(self.OLD, status=status)

        assert _flagged() == set()

    def test_the_reason_says_not_shipped_or_in_transit(self):
        pending = _placed(self.OLD, status=OrderStatus.PENDING)
        processing = _placed(self.OLD, status=OrderStatus.PROCESSING)
        shipped = _placed(self.OLD, status=OrderStatus.SHIPPED)
        delivered = _placed(self.OLD, status=OrderStatus.DELIVERED)

        with translation.override("en"):
            assert str(follow_up_reason(pending)) == "Not shipped yet"
            assert str(follow_up_reason(processing)) == "Not shipped yet"
            assert str(follow_up_reason(shipped)) == "In transit"
        assert follow_up_reason(delivered) is None


class TestUnpaidOnlineOrders:
    """An unpaid card order is an abandoned checkout, not a parcel."""

    OLD = datetime(2026, 9, 1, 12, 0, tzinfo=ATHENS)

    def _online(self, payment_status, **fields):
        order = OrderFactory(
            pay_way=PayWayFactory.create(
                active=True, settlement=PaySettlement.ONLINE
            ),
            payment_status=payment_status,
            status=OrderStatus.PROCESSING,
            num_order_items=0,
            **fields,
        )
        Order.objects.filter(pk=order.pk).update(created_at=self.OLD)
        return order

    @pytest.mark.parametrize(
        "payment_status", [PaymentStatus.PENDING, PaymentStatus.FAILED]
    )
    def test_unpaid_is_excluded(self, payment_status):
        _days_setting(7)
        self._online(payment_status)

        assert _flagged() == set()

    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.COMPLETED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_settled_online_orders_are_flagged(self, payment_status):
        """Includes a partially refunded order: ``awaits_online_payment``
        is true for it, but it is not waiting for money."""
        _days_setting(7)
        order = self._online(payment_status)

        assert _flagged() == {order.pk}

    def test_cash_on_delivery_with_a_pending_payment_is_flagged(self):
        _days_setting(7)
        order = _placed(
            self.OLD,
            payment_status=PaymentStatus.PENDING,
        )

        assert _flagged() == {order.pk}

    def test_an_order_without_a_pay_way_is_flagged(self):
        _days_setting(7)
        order = OrderFactory(
            pay_way=None, status=OrderStatus.PENDING, num_order_items=0
        )
        Order.objects.filter(pk=order.pk).update(created_at=self.OLD)

        assert _flagged() == {order.pk}


class TestAwaitingOnlinePayment:
    def test_the_queryset_is_the_database_form_of_the_rule(self):
        online = PayWayFactory.create(
            active=True, settlement=PaySettlement.ONLINE
        )
        unpaid = OrderFactory(
            pay_way=online,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        OrderFactory(
            pay_way=online,
            payment_status=PaymentStatus.COMPLETED,
            num_order_items=0,
        )
        OrderFactory(
            pay_way=online,
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
            num_order_items=0,
        )
        courier_cash_order(payment_status=PaymentStatus.PENDING)

        assert list(Order.objects.awaiting_online_payment()) == [unpaid]


class TestAnnotation:
    def test_the_annotation_agrees_with_the_filter(self):
        _days_setting(7)
        due = _placed(datetime(2026, 10, 1, 12, 0, tzinfo=ATHENS))
        _placed(datetime(2026, 10, 16, 12, 0, tzinfo=ATHENS))

        annotated = {
            order.pk: order.needs_follow_up
            for order in Order.objects.with_follow_up(NOW)
        }

        assert annotated[due.pk] is True
        assert sum(annotated.values()) == 1
        assert {pk for pk, flag in annotated.items() if flag} == _flagged()


class TestLastCarrierEvent:
    def test_the_latest_carrier_event_wins(self):
        order = courier_cash_order(status=OrderStatus.SHIPPED)
        assert last_carrier_event_at(order) is None

        event = datetime(2026, 10, 12, 9, 0, tzinfo=ATHENS)
        AcsShipmentFactory(order=order, last_event_at=event)
        order = Order.objects.with_shipments().get(pk=order.pk)

        assert last_carrier_event_at(order) == event
