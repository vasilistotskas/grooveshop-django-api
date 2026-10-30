"""What checkout and shipping snapshot for the turnover report.

``OrderVatLine`` rows are written when an order is created, by the code
the receipt is built with; ``Order.shipped_at`` is stamped the first time
the order is in a dispatched status. The backfills give the same to orders
that predate both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.apps import apps
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models import Order, OrderHistory, OrderVatLine
from order.services import OrderService
from order.taxation import ChargeKind, order_vat_lines
from order.vat_snapshot import (
    backfill_shipped_at,
    backfill_vat_lines,
    write_vat_lines,
)
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.discounts import snapshot_discounts
from tests.utils.legal import checkout_acceptance
from tests.utils.shipping import enable_rate
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal


def _at(month, day=15) -> datetime:
    return datetime(2026, month, day, 12, tzinfo=UTC)


def _order(**fields):
    order = OrderFactory(
        num_order_items=0,
        shipping_price=Money(D("12.40"), "EUR"),
        payment_method_fee=Money(D("3.10"), "EUR"),
        **fields,
    )
    order.items.all().delete()
    for gross, rate in ((D("124.00"), D("24")), (D("56.50"), D("13"))):
        order.items.create(
            product=ProductFactory(vat=VatFactory(value=rate)),
            price=Money(gross, "EUR"),
            quantity=1,
            vat_rate=rate,
        )
    order.discount_amount = Money(0, "EUR")
    order.loyalty_discount = Money(0, "EUR")
    order.save()
    snapshot_discounts(order)
    return order


def _as_lines(order):
    return sorted(
        (line.kind, line.vat_rate, line.net, line.vat)
        for line in order.vat_lines.all()
    )


class TestWriteVatLines:
    def test_the_rows_are_the_receipts_lines(self):
        order = _order()

        write_vat_lines(order)

        assert _as_lines(order) == sorted(
            (line.kind, line.rate, line.net, line.vat)
            for line in order_vat_lines(order)
        )
        kinds = {line.kind for line in order.vat_lines.all()}
        assert kinds == set(ChargeKind.values)

    def test_a_goods_row_names_its_item_and_a_charge_names_none(self):
        order = _order()

        write_vat_lines(order)

        goods = order.vat_lines.filter(kind=ChargeKind.GOODS)
        assert {line.item_id for line in goods} == {
            item.pk for item in order.items.all()
        }
        assert all(
            line.item_id is None
            for line in order.vat_lines.exclude(kind=ChargeKind.GOODS)
        )

    def test_writing_again_replaces_the_rows(self):
        order = _order()
        write_vat_lines(order)
        before = _as_lines(order)

        write_vat_lines(order)

        assert _as_lines(order) == before

    def test_deleting_an_item_takes_its_line_with_it(self):
        order = _order()
        write_vat_lines(order)

        order.items.first().delete()

        assert order.vat_lines.filter(kind=ChargeKind.GOODS).count() == 1


class TestCheckoutWritesTheLines:
    def test_an_offline_order_has_its_lines(self):
        cart = CartFactory(is_guest=True)
        cart.items.all().delete()
        CartItemFactory(
            cart=cart,
            product=ProductFactory(
                stock=10,
                price=Money(D("10.00"), "EUR"),
                discount_percent=D(0),
                active=True,
            ),
            quantity=2,
        )
        country = CountryFactory()
        enable_rate(country)

        order = OrderService.create_order_from_cart_offline(
            legal_acceptance=checkout_acceptance(),
            cart=cart,
            shipping_address={
                "first_name": "Maria",
                "last_name": "Papadopoulou",
                "email": "maria@example.com",
                "street": "Ermou",
                "street_number": "1",
                "city": "Athens",
                "zipcode": "10563",
                "country_id": country.alpha_2,
                "phone": "+306900000000",
                "shipping_kind": "home_delivery",
            },
            pay_way=PayWayFactory(
                settlement=PaySettlement.COURIER_CASH,
                active=True,
                cost=Money(D("2.00"), "EUR"),
                free_threshold=Money(D(0), "EUR"),
            ),
            user=None,
        )

        assert order.vat_lines.exists()
        assert _as_lines(order) == sorted(
            (line.kind, line.rate, line.net, line.vat)
            for line in order_vat_lines(order)
        )
        assert order.shipped_at is None


class TestShippedAt:
    def test_the_first_save_into_a_dispatched_status_stamps_it(self):
        order = OrderFactory(num_order_items=0, status=OrderStatus.PROCESSING)
        assert order.shipped_at is None

        OrderService.update_order_status(order, OrderStatus.SHIPPED)

        order.refresh_from_db()
        assert order.shipped_at == order.status_updated_at

    def test_a_later_status_never_moves_it(self):
        order = OrderFactory(num_order_items=0, status=OrderStatus.PROCESSING)
        OrderService.update_order_status(order, OrderStatus.SHIPPED)
        order.refresh_from_db()
        shipped = order.shipped_at

        OrderService.update_order_status(order, OrderStatus.DELIVERED)
        OrderService.update_order_status(order, OrderStatus.COMPLETED)

        order.refresh_from_db()
        assert order.shipped_at == shipped

    def test_a_partial_save_still_writes_it(self):
        order = OrderFactory(num_order_items=0, status=OrderStatus.PROCESSING)

        order.status = OrderStatus.DELIVERED
        order.save(update_fields=["status"])

        order.refresh_from_db()
        assert order.shipped_at is not None

    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
            OrderStatus.CANCELED,
        ],
    )
    def test_an_order_that_did_not_leave_the_store_has_none(self, status):
        order = OrderFactory(num_order_items=0, status=OrderStatus.PENDING)

        order.status = status
        order.save()

        order.refresh_from_db()
        assert order.shipped_at is None

    def test_an_order_created_already_shipped_is_stamped(self):
        order = OrderFactory(num_order_items=0, status=OrderStatus.SHIPPED)

        assert order.shipped_at is not None


class TestBackfillVatLines:
    def test_an_order_without_lines_gets_the_receipts(self):
        order = _order()

        written, skipped = backfill_vat_lines(apps)

        assert (written, skipped) == (1, [])
        assert _as_lines(order) == sorted(
            (line.kind, line.rate, line.net, line.vat)
            for line in order_vat_lines(order)
        )

    def test_it_is_idempotent(self):
        _order()
        backfill_vat_lines(apps)

        assert backfill_vat_lines(apps) == (0, [])
        assert OrderVatLine.objects.count() == 6

    def test_an_order_whose_receipt_cannot_be_derived_is_named_not_guessed(
        self,
    ):
        broken = _order()
        # The discount no longer adds up over the lines.
        Order.objects.filter(pk=broken.pk).update(
            discount_amount=Money(D("50.00"), "EUR")
        )
        fine = _order()

        written, skipped = backfill_vat_lines(apps)

        assert (written, skipped) == (1, [broken.pk])
        assert fine.vat_lines.exists()
        assert not broken.vat_lines.exists()


class TestBackfillShippedAt:
    def _history(self, order, status, when):
        row = OrderHistory.log_status_change(order, "PENDING", status)
        OrderHistory.objects.filter(pk=row.pk).update(created_at=when)

    def _unstamped(self, status=OrderStatus.COMPLETED):
        order = OrderFactory(num_order_items=0, status=status)
        Order.objects.filter(pk=order.pk).update(shipped_at=None)
        OrderHistory.objects.filter(order=order).delete()
        return order

    def test_the_first_dispatch_in_the_history_is_the_moment(self):
        order = self._unstamped()
        self._history(order, "PROCESSING", _at(2))
        self._history(order, "COMPLETED", _at(5))
        self._history(order, "SHIPPED", _at(3))
        self._history(order, "DELIVERED", _at(4))

        assert backfill_shipped_at(apps) == 1

        order.refresh_from_db()
        assert order.shipped_at == _at(3)

    def test_without_history_the_last_status_change_stands_in(self):
        order = self._unstamped()
        Order.objects.filter(pk=order.pk).update(status_updated_at=_at(6))

        backfill_shipped_at(apps)

        order.refresh_from_db()
        assert order.shipped_at == _at(6)

    def test_an_order_that_never_shipped_stays_without(self):
        order = self._unstamped(OrderStatus.PROCESSING)

        assert backfill_shipped_at(apps) == 0

        order.refresh_from_db()
        assert order.shipped_at is None

    def test_a_stamped_order_is_left_alone(self):
        order = self._unstamped()
        Order.objects.filter(pk=order.pk).update(shipped_at=_at(1))
        self._history(order, "SHIPPED", _at(3))

        assert backfill_shipped_at(apps) == 0

        order.refresh_from_db()
        assert order.shipped_at == _at(1)
