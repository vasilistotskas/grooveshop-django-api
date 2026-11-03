"""``GET /order/viva_return`` publishes the union it really answers.

A Smart Checkout return resolves to an order or to a gift-card purchase.
It used to be published as one object with every field optional, so the
storefront rebuilt the union by hand. The schema is now a ``oneOf``
discriminated on ``kind`` in which each branch requires its own fields.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from giftcard.factories import GiftCardPurchaseFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db

PATH = "/api/v1/order/viva_return"


def test_the_schema_is_a_union_discriminated_on_kind(openapi_schema):
    union = openapi_schema["components"]["schemas"]["VivaReturnLookupResponse"]

    assert union["discriminator"]["propertyName"] == "kind"
    assert set(union["discriminator"]["mapping"]) == {
        "order",
        "gift_card_purchase",
    }
    branches = {
        ref["$ref"].rsplit("/", 1)[-1]: openapi_schema["components"]["schemas"][
            ref["$ref"].rsplit("/", 1)[-1]
        ]
        for ref in union["oneOf"]
    }
    required = {name: set(spec["required"]) for name, spec in branches.items()}
    assert required == {
        "VivaReturnOrderResponse": {
            "kind",
            "id",
            "uuid",
            "status",
            "paymentStatus",
        },
        "VivaReturnGiftCardPurchaseResponse": {
            "kind",
            "purchaseUuid",
            "purchaseStatus",
        },
    }


def test_an_order_answer_satisfies_the_schema(documented):
    OrderFactory(
        num_order_items=0,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        payment_method="viva_wallet",
        payment_id="txn-contract-1",
    )

    response = APIClient().get(
        reverse("order-viva-return"), {"t": "txn-contract-1"}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["kind"] == "order"
    documented(response, PATH, "get")


def test_a_gift_card_purchase_answer_satisfies_the_schema(documented):
    GiftCardPurchaseFactory(
        payment_id="7680701046572601", provider_code="viva_wallet"
    )

    response = APIClient().get(
        reverse("order-viva-return"), {"s": "7680701046572601"}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["kind"] == "gift_card_purchase"
    documented(response, PATH, "get")
