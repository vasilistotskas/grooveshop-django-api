"""A signed-in customer's alert is one row per kind and product, kept
after it fires: asking again re-arms it, and only a live one is a 409."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]


@pytest.fixture
def product():
    return ProductFactory(active=True, price_drop_alerts_enabled=True)


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(UserAccountFactory(num_addresses=0))
    return api


def _ask(client, product, **extra):
    return client.post(
        reverse("product-alert-list"),
        {"kind": ProductAlertKind.RESTOCK, "product": product.pk, **extra},
        format="json",
    )


def test_asking_again_after_the_alert_fired_re_arms_it(client, product):
    assert _ask(client, product).status_code == status.HTTP_201_CREATED
    ProductAlert.objects.update(is_active=False, notified_at=timezone.now())

    response = _ask(client, product)

    assert response.status_code == status.HTTP_201_CREATED
    alert = ProductAlert.objects.get()
    assert alert.is_live
    assert alert.notified_at is None


def test_asking_again_while_the_alert_is_live_is_a_409(client, product):
    assert _ask(client, product).status_code == status.HTTP_201_CREATED

    response = _ask(client, product)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "alert_exists"
    assert ProductAlert.objects.count() == 1


def test_re_arming_a_price_drop_takes_the_new_target(client, product):
    price = product.price.amount
    first = {
        "kind": ProductAlertKind.PRICE_DROP,
        "targetPrice": str(price - 2),
        "targetPriceCurrency": "EUR",
    }
    assert _ask(client, product, **first).status_code == 201
    ProductAlert.objects.update(is_active=False, notified_at=timezone.now())

    response = _ask(
        client, product, **(first | {"targetPrice": str(price - 1)})
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert ProductAlert.objects.get().target_price.amount == price - 1
