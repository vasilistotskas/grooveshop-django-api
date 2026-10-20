"""Restocking a returned line, in a real store schema.

The staff member who puts units back is a PUBLIC-schema identity, while
the order, its lines, the product and the stock log live in the store's
schema. Postgres has no cross-schema foreign key, so the stock log names
staff by id in ``performed_by_staff_id`` (``tenant.staff_actors``) and
keeps ``performed_by`` for shoppers only: a foreign key would point the
public pk at the store's own user table and name a shopper. The main
suite runs every table in one schema and cannot see either.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django_tenants.utils import get_public_schema_name, schema_context
from djmoney.money import Money

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user = UserAccount.objects.create_user(
            email="mt-restock-staff@example.com",
            username="mtrestockstaff",
            password=None,
            is_staff=True,
        )
    # What ``PlatformStaffBackend`` stamps on every staff login.
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


@pytest.fixture
def delivered_line(mt_tenant):
    from order.enum.status import OrderStatus
    from order.factories import OrderFactory
    from order.factories.item import OrderItemFactory
    from product.factories.product import ProductFactory

    with schema_context(mt_tenant.schema_name):
        product = ProductFactory.create(stock=10, num_images=0, num_reviews=0)
        order = OrderFactory.create(
            num_order_items=0, status=OrderStatus.DELIVERED
        )
        item = OrderItemFactory.create(
            order=order,
            product=product,
            price=Money(Decimal("20.00"), "EUR"),
            quantity=3,
            refunded_quantity=0,
            is_refunded=False,
        )
        yield item


def test_the_public_staff_member_is_recorded_on_the_stores_stock_log(
    mt_tenant, delivered_line, staff
):
    from order.models.stock_log import StockLog

    with schema_context(mt_tenant.schema_name):
        delivered_line.refund(2, performed_by=staff, reason="Came back")

        log = StockLog.objects.get(
            order_id=delivered_line.order_id,
            operation_type=StockLog.OPERATION_INCREMENT,
        )
        assert log.performed_by_staff_id == staff.pk
        assert log.performed_by_id is None
        assert "Came back" in log.reason
        delivered_line.product.refresh_from_db()
        assert delivered_line.product.stock == 12


def test_a_second_pass_cannot_put_the_same_units_back(
    mt_tenant, delivered_line, staff
):
    with schema_context(mt_tenant.schema_name):
        delivered_line.refund(3, performed_by=staff)

        with pytest.raises(ValidationError):
            delivered_line.refund(1, performed_by=staff)

        delivered_line.product.refresh_from_db()
        assert delivered_line.product.stock == 13
