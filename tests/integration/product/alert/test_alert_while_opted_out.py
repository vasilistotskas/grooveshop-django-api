"""A signed-in customer who opted out cannot make an alert that
could never be delivered; a guest is told nothing about an address."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

GUEST = "guest-optout@example.com"


@pytest.fixture
def product():
    return ProductFactory(active=True, price_drop_alerts_enabled=True)


def _payload(product, kind=ProductAlertKind.RESTOCK, **extra):
    body = {"kind": kind, "product": product.pk, **extra}
    if kind == ProductAlertKind.PRICE_DROP:
        body |= {
            "targetPrice": str(product.price.amount - 1),
            "targetPriceCurrency": "EUR",
        }
    return body


def _post(client, body):
    return client.post(reverse("product-alert-list"), body, format="json")


@pytest.mark.parametrize("key", [EVERY_MARKETING_TYPE, "product.restock_alert"])
def test_a_signed_in_customer_who_opted_out_gets_a_409(product, key):
    user = UserAccountFactory(num_addresses=0)
    optout.opt_out(user.email, key, source=EmailOptOut.Source.LINK)
    client = APIClient()
    client.force_authenticate(user)

    response = _post(client, _payload(product))

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "email_opted_out"
    assert not ProductAlert.objects.exists()


def test_a_guest_gets_the_same_answer_for_an_opted_out_address(product):
    optout.opt_out(
        GUEST, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.ONE_CLICK
    )
    other = "guest-not-opted-out@example.com"

    opted_out = _post(APIClient(), _payload(product, email=GUEST))
    not_opted_out = _post(APIClient(), _payload(product, email=other))

    assert opted_out.status_code == status.HTTP_201_CREATED
    assert not_opted_out.status_code == opted_out.status_code
    assert not_opted_out.data.keys() == opted_out.data.keys()


def test_the_opt_out_of_one_kind_does_not_block_the_other(product):
    optout.opt_out(
        GUEST, "product.restock_alert", source=EmailOptOut.Source.LINK
    )

    response = _post(
        APIClient(), _payload(product, ProductAlertKind.PRICE_DROP, email=GUEST)
    )

    assert response.status_code == status.HTTP_201_CREATED


def test_an_address_that_did_not_opt_out_still_gets_a_201(product):
    response = _post(APIClient(), _payload(product, email=GUEST))

    assert response.status_code == status.HTTP_201_CREATED
