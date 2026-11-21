"""An order placed with one street line (#641).

A caller that holds an address as one line (the UCP and ACP shipping
destinations have a single street line) sends it as ``street`` with no
``streetNumber``. Django splits it once, so the order stores the two
columns the courier voucher reads, and refuses a line it cannot split
with a code on ``street``.
"""

from __future__ import annotations

import pytest

from order.models.order import Order
from tests.integration.order.test_checkout_quote import _checkout, _post_order

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]


def _one_line(checkout, street: str):
    body = {"street": street, "street_number": ""}
    return _post_order(checkout, **body)


def test_the_order_stores_the_street_and_its_number():
    checkout = _checkout()

    response = _one_line(checkout, "Akadimias 3A")

    assert response.status_code == 201, response.content
    order = Order.objects.get()
    assert (order.street, order.street_number) == ("Akadimias", "3A")


def test_a_line_without_a_number_is_a_field_error(documented):
    checkout = _checkout()

    response = _one_line(checkout, "Akadimias")

    assert response.status_code == 400, response.content
    documented(response, "/api/v1/order", "post")
    body = response.json()
    assert body["fields"]["street"][0]["code"] == "street_number_missing"
    assert "streetNumber" not in body["fields"]
    assert not Order.objects.exists()
