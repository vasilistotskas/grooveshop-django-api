"""The owner cannot move a priced order to another country or region.

Shipping and VAT are priced for the delivery country and region when the
order is created (and, for an online order, charged). ``PATCH``/``PUT``
on the order let the owner change either afterwards, so the parcel went
somewhere the amount paid never covered. The rest of the address stays
editable: it reprices nothing.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from country.factories import CountryFactory
from order.factories.order import OrderFactory
from order.models.order import Order
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def owner():
    return UserAccountFactory(num_addresses=0)


@pytest.fixture
def order(owner):
    country = CountryFactory()
    return OrderFactory(
        user=owner,
        country=country,
        region=RegionFactory(country=country),
        num_order_items=0,
    )


def _client(user):
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)
    return client


def _patch(owner, order, body):
    return _client(owner).patch(
        reverse("order-detail", args=[order.pk]), body, format="json"
    )


def test_the_country_cannot_change(owner, order):
    other = CountryFactory()

    response = _patch(owner, order, {"country": other.alpha_2})

    assert response.status_code == 400, response.content
    assert "country" in response.json()
    assert Order.objects.get(pk=order.pk).country_id == order.country_id


def test_the_region_cannot_change(owner, order):
    other = RegionFactory(country=order.country)

    response = _patch(owner, order, {"region": other.alpha})

    assert response.status_code == 400, response.content
    assert "region" in response.json()
    assert Order.objects.get(pk=order.pk).region_id == order.region_id


def test_restating_the_stored_country_and_region_is_not_a_change(owner, order):
    response = _patch(
        owner,
        order,
        {"country": order.country_id, "region": order.region_id},
    )

    assert response.status_code == 200, response.content


def test_the_rest_of_the_address_is_still_editable(owner, order):
    response = _patch(owner, order, {"city": "Thessaloniki"})

    assert response.status_code == 200, response.content
    assert Order.objects.get(pk=order.pk).city == "Thessaloniki"
