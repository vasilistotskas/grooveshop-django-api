"""The order API says what the customer owes, and what is left of it.

``paidAmount`` held the amount OWED, so the storefront could not tell an
unpaid online order from a paid one: ``pricingBreakdown.remainingAmount``
was 0 on every order and the "pay €X" state never showed. The field is
``amountDue`` now, and ``remainingAmount`` is what is still to be paid:
all of it until the payment settles.

``paidAmount`` (top level and in ``pricingBreakdown``) stays for ONE
release as a deprecated alias of ``amountDue``: the storefront's and the
agent gateway's generated schemas require it, so removing it in the release
that adds ``amountDue`` would fail every order response they parse between
the two rollouts. The filters and the ordering are ``amountDue`` only:
nothing outside Django uses the ``paidAmount`` ones.
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


@pytest.mark.parametrize(
    "payment_status", [PaymentStatus.PENDING, PaymentStatus.COMPLETED]
)
def test_the_deprecated_paid_amount_equals_amount_due(user, payment_status):
    """Top level and in the breakdown, so a client still generated from the
    previous schema parses the response and reads what it always read."""
    body = _detail(_order(user, payment_status))

    assert _number(body["paidAmount"]) == _number(body["amountDue"]) == DUE
    breakdown = body["pricingBreakdown"]
    assert (
        _number(breakdown["paidAmount"])
        == _number(breakdown["amountDue"])
        == DUE
    )


def test_the_list_carries_the_deprecated_alias_too(user):
    order = _order(user, PaymentStatus.PENDING)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get(reverse("order-list"))

    assert response.status_code == status.HTTP_200_OK
    [row] = [r for r in response.json()["results"] if r["id"] == order.pk]
    assert _number(row["amountDue"]) == _number(row["paidAmount"]) == DUE


def _order_components(openapi_schema) -> dict[str, dict]:
    """The component schemas that expose ``amountDue``: the order list
    and detail responses."""
    return {
        name: schema
        for name, schema in openapi_schema["components"]["schemas"].items()
        if "amountDue" in schema.get("properties", {})
    }


def test_the_schema_exposes_amount_due_and_deprecates_paid_amount(
    openapi_schema,
):
    """The contract the storefront and the agent gateway generate from."""
    components = _order_components(openapi_schema)

    assert components, "no schema exposes amountDue"
    for name, schema in components.items():
        properties = schema["properties"]
        assert "deprecated" not in properties["amountDue"], name
        assert properties["paidAmount"]["deprecated"] is True, name
        # Still required, as it was: a client generated from the previous
        # schema parses these responses until the contract release.
        assert "paidAmount" in schema["required"], name


def test_the_breakdown_schema_deprecates_paid_amount_too(openapi_schema):
    detail = openapi_schema["components"]["schemas"]["OrderDetail"]
    breakdown = detail["properties"]["pricingBreakdown"]["properties"]

    assert "deprecated" not in breakdown["amountDue"]
    assert breakdown["paidAmount"]["deprecated"] is True
    assert "remainingAmount" in breakdown


def test_the_filters_and_ordering_are_amount_due_only(openapi_schema):
    parameters = [
        parameter
        for path in openapi_schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        for parameter in operation.get("parameters", [])
    ]
    names = {parameter["name"] for parameter in parameters}

    assert {"amountDueMin", "amountDueMax"} <= names
    assert not {name for name in names if name.startswith("paidAmount")}
    for parameter in parameters:
        assert "paidAmount" not in str(parameter.get("schema", "")), parameter
        assert "paidAmount" not in parameter.get("description", ""), parameter
