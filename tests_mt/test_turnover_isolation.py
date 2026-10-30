"""A store's turnover is read from its own schema only.

The report aggregates orders, lines and the payment ledger with no tenant
filter of its own: the schema the connection is bound to is the whole
scope. This pins that a sale in one store is nowhere in another's figures,
on the real router.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django_tenants.utils import schema_context
from djmoney.money import Money

pytestmark = pytest.mark.django_db

YEAR = 2026


@pytest.fixture
def sale(mt_tenant):
    from order.enum.status import OrderStatus, PaymentStatus
    from order.factories.order import OrderFactory
    from order.models import Order
    from product.factories.product import ProductFactory
    from vat.factories import VatFactory

    with schema_context(mt_tenant.schema_name):
        order = OrderFactory.create(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            shipping_price=Money(Decimal("10.00"), "EUR"),
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
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=datetime(YEAR, 3, 15, 12, tzinfo=UTC)
        )
        yield order
        order.items.all().delete()
        order.delete()


def test_a_sale_is_in_its_own_stores_figures(mt_tenant, sale):
    from order.turnover import turnover_by_month

    with schema_context(mt_tenant.schema_name):
        march = turnover_by_month(YEAR)[2]

    assert march.sales == Decimal("100.00")
    assert march.orders == 1


def test_a_sale_is_not_in_another_stores_figures(mt_tenant, mt_tenant_b, sale):
    from order.turnover import first_year, turnover_by_month

    with schema_context(mt_tenant_b.schema_name):
        months = turnover_by_month(YEAR)
        assert first_year() is None

    assert all(m.sales == m.returns == Decimal(0) for m in months)
    assert sum(m.orders for m in months) == 0
