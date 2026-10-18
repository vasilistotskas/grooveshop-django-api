"""Who may review a product: ``PRODUCT_REVIEWS_PURCHASE_REQUIRED``.

On (the default) only a customer with a completed order for the product
may review it. Off, every signed-in customer may, and a buyer's review
still carries the verified-purchase badge.
"""

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework.test import APIClient

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from product.models.review import ProductReview
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _review(client, product):
    return client.post(
        reverse("product-review-list"),
        data={
            "product": product.id,
            "rate": 4,
            "translations": {
                settings.PARLER_DEFAULT_LANGUAGE_CODE: {"comment": "Fine."}
            },
        },
        format="json",
    )


def _signed_in(*, bought=None):
    user = UserAccountFactory(num_addresses=0, is_superuser=False)
    if bought is not None:
        order = OrderFactory.create(
            user=user, status=OrderStatus.COMPLETED, num_order_items=0
        )
        OrderItemFactory.create(order=order, product=bought, quantity=1)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def product():
    return ProductFactory(num_images=0, num_reviews=0, active=True)


def test_a_customer_who_did_not_buy_is_refused_by_default(product):
    response = _review(_signed_in(), product)

    assert response.status_code == 400
    assert "product" in response.data
    assert not ProductReview.objects.filter(product=product).exists()


def test_any_signed_in_customer_may_review_when_the_store_allows_it(product):
    switch_store_settings(PRODUCT_REVIEWS_PURCHASE_REQUIRED=False)

    response = _review(_signed_in(), product)

    assert response.status_code == 201
    review = ProductReview.objects.with_verified_purchase().get(
        pk=response.data["id"]
    )
    assert review.is_verified_purchase is False


def test_a_buyer_keeps_the_verified_badge_when_anyone_may_review(product):
    switch_store_settings(PRODUCT_REVIEWS_PURCHASE_REQUIRED=False)

    response = _review(_signed_in(bought=product), product)

    assert response.status_code == 201
    review = ProductReview.objects.with_verified_purchase().get(
        pk=response.data["id"]
    )
    assert review.is_verified_purchase is True


def test_a_guest_still_cannot_review(product):
    switch_store_settings(PRODUCT_REVIEWS_PURCHASE_REQUIRED=False)

    response = _review(APIClient(), product)

    assert response.status_code in (401, 403)
