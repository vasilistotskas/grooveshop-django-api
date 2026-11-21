"""The order's address line 2 (#641): a flat, a floor, a building.

UCP's shipping destination carries ``extended_address`` beside its one
street line; a buyer types it in a second address input. The order keeps
it as ``address_line_2``, apart from the customer's notes: on the order
answer, under the street on the receipt and the order emails, and in the
ACS voucher's ``Delivery_Notes`` (``tests/unit/shipping_acs``).
"""

from __future__ import annotations

import pytest

from order.accounting.invoicing import _buyer_snapshot
from order.models.order import Order
from order.serializers.order import OrderCreateFromCartSerializer
from tests.integration.order.test_checkout_quote import _checkout, _post_order

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]


def test_an_order_keeps_it_apart_from_the_notes(documented):
    checkout = _checkout()

    response = _post_order(
        checkout,
        address_line_2="Διαμ. 3, 2ος όροφος",
        customer_notes="Θυρωρός",
    )

    assert response.status_code == 201, response.content
    documented(response, "/api/v1/order", "post")
    order = Order.objects.get()
    assert order.address_line_2 == "Διαμ. 3, 2ος όροφος"
    assert order.customer_notes == "Θυρωρός"
    assert response.json()["addressLine2"] == "Διαμ. 3, 2ος όροφος"


def test_without_it_the_order_has_none():
    checkout = _checkout()

    response = _post_order(checkout)

    assert response.status_code == 201, response.content
    assert Order.objects.get().address_line_2 == ""


def test_it_is_capped_at_the_columns_length():
    field = OrderCreateFromCartSerializer().fields["address_line_2"]

    assert (
        field.max_length == Order._meta.get_field("address_line_2").max_length
    )


def test_the_receipt_prints_it_under_the_street():
    checkout = _checkout()
    _post_order(checkout, address_line_2="Διαμ. 3")
    order = Order.objects.get()

    snapshot = _buyer_snapshot(order)

    assert snapshot["address_line_2"] == "Διαμ. 3"


@pytest.mark.parametrize(
    "key",
    [
        "order.received",
        "order.payment_confirmed",
        "order.shipped",
        "order.delivered",
    ],
)
def test_the_order_emails_print_it_under_the_street(key):
    from emails.recipients import Recipient
    from emails.registry import registry
    from emails.render import render
    from emails.types import RenderEnv

    email_type = registry.get(key)
    env = RenderEnv(
        language="en", recipient=Recipient("customer@example.test", "en")
    )
    order = email_type.sample(env)
    order.address_line_2 = "Flat 3, 2nd floor"

    rendered = render(email_type, order, env)

    for body in (rendered.text, rendered.html):
        street = body.index(order.street)
        assert (
            street
            < body.index("Flat 3, 2nd floor")
            < body.index(order.city, street)
        )
