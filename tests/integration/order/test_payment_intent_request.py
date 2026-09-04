"""The payment-intent request body must not be able to 500 the endpoint,
and the fields the view reads must actually arrive.

``payment_data`` is splatted into ``PayWayService.process_payment`` as
keyword arguments. A key matching one of that call's own parameters
raised ``TypeError`` — an uncaught 500 on a payment endpoint, reachable
by the order's owner or a guest with the uuid.

Separately, the view copies ``payment_method_id`` / ``customer_id`` /
``return_url`` off ``validated_data``, but the serializer never declared
them, so DRF dropped them and those branches were dead.
"""

from __future__ import annotations

import pytest

from order.serializers.order import CreatePaymentIntentRequestSerializer

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("key", ["order", "amount", "pay_way", "order_id"])
def test_a_reserved_key_is_rejected_not_crashed(key):
    serializer = CreatePaymentIntentRequestSerializer(
        data={"payment_data": {key: "1"}}
    )

    assert not serializer.is_valid()
    assert "payment_data" in serializer.errors


def test_ordinary_payment_data_still_passes():
    serializer = CreatePaymentIntentRequestSerializer(
        data={"payment_data": {"save_card": "true"}}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["payment_data"] == {"save_card": "true"}


def test_the_named_fields_survive_validation():
    """Undeclared, DRF dropped these and the view's copy branches could
    never fire."""
    serializer = CreatePaymentIntentRequestSerializer(
        data={
            "payment_method_id": "pm_123",
            "customer_id": "cus_123",
            "return_url": "https://shop.example/checkout/return",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["payment_method_id"] == "pm_123"
    assert serializer.validated_data["customer_id"] == "cus_123"
    assert (
        serializer.validated_data["return_url"]
        == "https://shop.example/checkout/return"
    )
