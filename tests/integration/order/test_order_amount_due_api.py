"""The order API says what the customer owes, and what is left of it.

``paidAmount`` held the amount OWED, so the storefront could not tell an
unpaid online order from a paid one: ``pricingBreakdown.remainingAmount``
was 0 on every order and the "pay €X" state never showed. The field is
``amountDue`` now (no alias: the app is in development), and
``remainingAmount`` is what is still to be paid: all of it until the
payment settles.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.factories.payment_transaction import PaymentTransactionFactory
from pay_way.factories import PayWayFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DUE = Decimal("50.00")


def _order(user, payment_status, **kwargs):
    return OrderFactory(
        user=user,
        num_order_items=0,
        status=OrderStatus.PENDING,
        payment_status=payment_status,
        pay_way=PayWayFactory.create_online_payment("stripe"),
        amount_due=Money(DUE, "EUR"),
        **kwargs,
    )


def _detail(order) -> dict:
    client = APIClient()
    client.force_authenticate(user=order.user)
    response = client.get(reverse("order-detail", args=[order.pk]))
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def _number(value) -> Decimal:
    return Decimal(str(value))


@pytest.fixture
def user():
    return UserAccountFactory(num_addresses=0)


def test_an_unpaid_online_order_still_owes_its_whole_amount(user):
    body = _detail(_order(user, PaymentStatus.PENDING))

    assert _number(body["amountDue"]) == DUE
    assert body["isPaid"] is False
    breakdown = body["pricingBreakdown"]
    assert _number(breakdown["amountDue"]) == DUE
    assert _number(breakdown["remainingAmount"]) == DUE


def test_a_paid_order_keeps_its_amount_due_and_owes_nothing(user):
    order = _order(user, PaymentStatus.COMPLETED)
    PaymentTransactionFactory(order=order, amount=Money(DUE, "EUR"))

    body = _detail(order)

    assert _number(body["amountDue"]) == DUE
    assert body["isPaid"] is True
    assert _number(body["pricingBreakdown"]["remainingAmount"]) == 0


def test_an_order_settled_without_a_ledger_row_owes_nothing(user):
    """Paid by hand, or under a payment method the ledger backfill does
    not key on: no CHARGE or COD_COLLECTION row, and it is still paid."""
    order = _order(user, PaymentStatus.COMPLETED)
    assert not order.payment_transactions.exists()

    body = _detail(order)

    assert body["isPaid"] is True
    assert _number(body["pricingBreakdown"]["remainingAmount"]) == 0


def test_the_old_name_is_gone_from_the_response(user):
    body = _detail(_order(user, PaymentStatus.PENDING))

    assert "paidAmount" not in body
    assert "paidAmount" not in body["pricingBreakdown"]


def test_the_old_name_is_gone_from_the_schema(openapi_schema):
    """The contract the storefront and the agent gateway generate from."""
    schemas = openapi_schema["components"]["schemas"]
    order_schemas = [
        name
        for name, schema in schemas.items()
        if "amountDue" in schema.get("properties", {})
    ]

    assert order_schemas, "no schema exposes amountDue"
    for name, schema in schemas.items():
        assert "paidAmount" not in schema.get("properties", {}), name
    parameter_names = {
        parameter["name"]
        for path in openapi_schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        for parameter in operation.get("parameters", [])
    }
    assert not {name for name in parameter_names if "paidAmount" in name}
    assert {"amountDueMin", "amountDueMax"} <= parameter_names
