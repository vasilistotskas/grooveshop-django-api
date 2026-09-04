"""The 0050 backfill must leave every invoice's lines summing to its total.

The three component columns are added empty, and the PDF template renders
them behind ``{% if %}`` while printing the total unconditionally — so an
invoice left at zero prints a total containing a shipping and a fee that
have no lines. On an AADE-registered document that is a filed total its
own lines contradict.

The split is derived from the invoice, not from the order's current
values: ``_order_totals`` guarantees ``total == subtotal + vat + shipping
+ fee``, so the leftover IS the two components. Trusting the order's
live values could contradict a total frozen at issue time.
"""

from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from djmoney.money import Money

from order.factories.order import OrderFactory
from order.models.invoice import Invoice

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "order.migrations.0050_backfill_invoice_components"
)


def _order(shipping="3.50", fee="1.25"):
    order = OrderFactory(num_order_items=0)
    order.shipping_price = Money(Decimal(shipping), "EUR")
    order.payment_method_fee = Money(Decimal(fee), "EUR")
    order.save(update_fields=["shipping_price", "payment_method_fee"])
    return order


def _invoice(order, *, subtotal, vat, total, number="TEST-1"):
    return Invoice.objects.create(
        order=order,
        invoice_number=number,
        issue_date=date(2026, 1, 1),
        subtotal=Money(Decimal(subtotal), "EUR"),
        total_vat=Money(Decimal(vat), "EUR"),
        total=Money(Decimal(total), "EUR"),
        currency="EUR",
    )


def _historical_apps():
    """The model registry a migration actually receives.

    Not ``django.apps.apps``: historical models carry no custom
    ``__init__``, and ``Order.__init__`` reads ``self.payment_status``,
    which on a ``.only()``-deferred instance would reload the row and
    recurse. Running the backfill against live models would therefore
    test something the migration never does.
    """
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state(
        ("order", "0050_backfill_invoice_components")
    )
    return state.apps


def _run():
    _migration.backfill_components(_historical_apps(), None)


def _reconciles(invoice) -> bool:
    return (
        invoice.subtotal.amount
        + invoice.total_vat.amount
        + invoice.shipping.amount
        + invoice.payment_fee.amount
        == invoice.total.amount
    )


def test_splits_the_residual_using_the_orders_fee():
    order = _order()
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="17.15")

    _run()

    invoice.refresh_from_db()
    assert invoice.payment_fee.amount == Decimal("1.25")
    assert invoice.shipping.amount == Decimal("3.50")
    assert _reconciles(invoice)


def test_a_changed_order_never_contradicts_the_frozen_total():
    """The order's shipping moved after the invoice was registered."""
    order = _order(shipping="99.00", fee="1.25")
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="17.15")

    _run()

    invoice.refresh_from_db()
    # The frozen residual is 4.75, so shipping absorbs what is left after
    # the fee rather than the order's stale 99.00.
    assert invoice.payment_fee.amount == Decimal("1.25")
    assert invoice.shipping.amount == Decimal("3.50")
    assert _reconciles(invoice)


def test_a_fee_larger_than_the_residual_is_clamped():
    order = _order(shipping="0.00", fee="50.00")
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="14.40")

    _run()

    invoice.refresh_from_db()
    assert invoice.payment_fee.amount == Decimal("2.00")
    assert invoice.shipping.amount == Decimal("0.00")
    assert _reconciles(invoice)


def test_an_invoice_with_no_extras_stays_at_zero():
    order = _order(shipping="0.00", fee="0.00")
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="12.40")

    _run()

    invoice.refresh_from_db()
    assert invoice.shipping.amount == Decimal("0")
    assert invoice.payment_fee.amount == Decimal("0")
    assert _reconciles(invoice)


def test_running_twice_changes_nothing():
    order = _order()
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="17.15")

    _run()
    invoice.refresh_from_db()
    first = (invoice.shipping.amount, invoice.payment_fee.amount)
    _run()
    invoice.refresh_from_db()

    assert (invoice.shipping.amount, invoice.payment_fee.amount) == first
    assert _reconciles(invoice)


def test_currency_travels_with_the_amount():
    """``bulk_update`` has to carry the companion currency columns, or a
    non-EUR amount lands under the default label."""
    order = _order()
    invoice = _invoice(order, subtotal="10.00", vat="2.40", total="17.15")

    _run()

    invoice.refresh_from_db()
    assert invoice.shipping.currency.code == invoice.total.currency.code
    assert invoice.payment_fee.currency.code == invoice.total.currency.code
