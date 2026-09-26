"""An order's status changes only through the admin actions.

Production orders 242 and 213 (2026-09-08) were canceled by picking
CANCELED in the change form's status dropdown. The form wrote the column
straight to the row, so ``OrderService.cancel_order`` never ran: no stock
restored, no reservation released, and the transition table in
``update_order_status`` was bypassed too (any status could follow any
other). The field is now read-only and every transition is an action
routed through the service.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from order.admin import OrderAdmin
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db


def _admin():
    return OrderAdmin(Order, AdminSite())


def _request():
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=1, username="staff", is_superuser=True)
    return request


def _order(status, payment_status=PaymentStatus.PENDING):
    return OrderFactory(
        status=status,
        payment_status=payment_status,
        pay_way=PayWayFactory(),
        num_order_items=0,
    )


def test_the_change_form_cannot_edit_status():
    order = _order(OrderStatus.PENDING)
    admin = _admin()

    assert "status" in admin.get_readonly_fields(_request(), order)
    form_class = admin.get_form(_request(), order)
    assert "status" not in form_class.base_fields


def test_cancel_action_restores_the_stock_the_order_took():
    product = ProductFactory(stock=10, num_images=0, num_reviews=0)
    order = _order(OrderStatus.PROCESSING)
    StockManager.decrement_stock(
        product_id=product.id, quantity=2, order_id=order.id
    )
    product.refresh_from_db()
    assert product.stock == 8

    with patch.object(OrderAdmin, "message_user"):
        _admin().mark_as_canceled(_request(), Order.objects.filter(pk=order.pk))

    order.refresh_from_db()
    product.refresh_from_db()
    assert order.status == OrderStatus.CANCELED
    assert product.stock == 10


def test_returned_action_uses_the_transition_table_and_keeps_stock():
    """RETURNED leaves stock alone on purpose — the goods come back days
    later and may be damaged (docs/order-system.md §5.1) — so the action
    must not restock, only move the status the legal way."""
    product = ProductFactory(stock=10, num_images=0, num_reviews=0)
    shipped = _order(OrderStatus.SHIPPED)
    StockManager.decrement_stock(
        product_id=product.id, quantity=1, order_id=shipped.id
    )
    pending = _order(OrderStatus.PENDING)

    with patch.object(OrderAdmin, "message_user") as message:
        _admin().mark_as_returned(
            _request(), Order.objects.filter(pk__in=[shipped.pk, pending.pk])
        )

    shipped.refresh_from_db()
    pending.refresh_from_db()
    product.refresh_from_db()
    assert shipped.status == OrderStatus.RETURNED
    # An unpaid return settles its payment (``Order.settle_unpaid_payment``).
    assert shipped.payment_status == PaymentStatus.CANCELED
    assert product.stock == 9
    # PENDING → RETURNED is not a legal transition: skipped and reported.
    assert pending.status == OrderStatus.PENDING
    assert message.call_count == 2


def test_refunded_action_only_follows_a_return():
    returned = _order(OrderStatus.RETURNED, PaymentStatus.REFUNDED)
    completed = _order(OrderStatus.COMPLETED, PaymentStatus.COMPLETED)

    with patch.object(OrderAdmin, "message_user"):
        _admin().mark_as_refunded(
            _request(),
            Order.objects.filter(pk__in=[returned.pk, completed.pk]),
        )

    returned.refresh_from_db()
    completed.refresh_from_db()
    assert returned.status == OrderStatus.REFUNDED
    assert completed.status == OrderStatus.COMPLETED
