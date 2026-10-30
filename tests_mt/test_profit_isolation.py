"""A store's profit is read from its own schema only.

The report aggregates lines, the payment ledger and the expenses with no
tenant filter of its own: the schema the connection is bound to is the whole
scope. This pins that a cost, a fee and an expense of one store are nowhere
in another's figures, on the real router.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django_tenants.utils import schema_context
from djmoney.money import Money

from order.profit import figures

pytestmark = pytest.mark.django_db

YEAR = 2026


@pytest.fixture
def sale(mt_tenant):
    from order.enum.expense import ExpenseCategory
    from order.enum.status import OrderStatus, PaymentStatus
    from order.factories.expense import ExpenseFactory
    from order.factories.order import OrderFactory
    from product.factories.product import ProductFactory
    from tests.utils.turnover import snapshot_sale
    from vat.factories import VatFactory

    with schema_context(mt_tenant.schema_name):
        order = OrderFactory.create(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            shipping_price=Money(Decimal(0), "EUR"),
            payment_method_fee=Money(Decimal(0), "EUR"),
        )
        order.items.all().delete()
        order.items.create(
            product=ProductFactory.create(
                vat=VatFactory.create(value=Decimal(24)),
                num_images=0,
                num_reviews=0,
            ),
            price=Money(Decimal("124.00"), "EUR"),
            quantity=1,
            vat_rate=Decimal(24),
            unit_cost=Money(Decimal("30.00"), "EUR"),
        )
        snapshot_sale(order, datetime(YEAR, 3, 15, 12, tzinfo=UTC))
        expense = ExpenseFactory.create(
            date=date(YEAR, 3, 5),
            category=ExpenseCategory.SHIPPING,
            amount_net=Money(Decimal("25.00"), "EUR"),
        )
        yield order
        expense.delete()
        order.items.all().delete()
        order.delete()


def test_a_cost_and_an_expense_are_in_their_own_stores_figures(mt_tenant, sale):
    with schema_context(mt_tenant.schema_name):
        [march] = figures(date(YEAR, 3, 1), date(YEAR, 3, 31), None)

    assert march.cogs == Decimal("30.00")
    assert march.delivery_costs == Decimal("25.00")
    assert march.operating_profit == Decimal("45.00")


def test_they_are_not_in_another_stores_figures(mt_tenant, mt_tenant_b, sale):
    with schema_context(mt_tenant_b.schema_name):
        [year] = figures(date(YEAR, 1, 1), date(YEAR, 12, 31), None)

    assert year.revenue == year.cogs == year.delivery_costs == Decimal(0)
    assert year.operating_profit == Decimal(0)
