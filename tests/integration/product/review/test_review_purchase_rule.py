"""Who may review a product: ``PRODUCT_REVIEWS_PURCHASE_REQUIRED``.

On (the default) only a customer with a completed order for the product
may review it. Off, every signed-in customer may, and a buyer's review
still carries the verified-purchase badge.
"""

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import translation
from rest_framework.test import APIClient

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from product.models.review import ProductReview
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _review(client, product, language=None):
    # The unprefixed route: under the pinned English a reverse() carries
    # the /en/ prefix, which would win over the X-Language header.
    with translation.override(settings.LANGUAGE_CODE):
        url = reverse("product-review-list")
    return client.post(
        url,
        data={
            "product": product.id,
            "rate": 4,
            "translations": {
                settings.PARLER_DEFAULT_LANGUAGE_CODE: {"comment": "Fine."}
            },
        },
        format="json",
        headers={"x-language": language} if language else {},
    )


def _signed_in(*, bought=None, status=OrderStatus.COMPLETED, pay_way=None):
    user = UserAccountFactory(num_addresses=0, is_superuser=False)
    if bought is not None:
        extra = {"pay_way": pay_way} if pay_way else {}
        order = OrderFactory.create(
            user=user, status=status, num_order_items=0, **extra
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


def test_the_refusal_names_a_stable_code_and_says_it_in_english(product):
    response = _review(_signed_in(), product, "en")

    body = response.json()
    assert body["code"] == "must_have_purchased"
    assert body["product"] == (
        "You must have purchased this product to review it."
    )
    assert body["fields"]["product"] == [
        {"message": body["product"], "code": "must_have_purchased"}
    ]


def test_the_refusal_is_said_in_the_shoppers_language(product):
    response = _review(_signed_in(), product, "el")

    body = response.json()
    assert body["code"] == "must_have_purchased"
    with translation.override("el"):
        greek = str(
            translation.gettext(
                "You must have purchased this product to review it."
            )
        )
    assert body["product"] == greek
    assert greek != "You must have purchased this product to review it."


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


@pytest.mark.parametrize(
    "settlement", sorted(PaySettlement.collected_on_delivery())
)
def test_a_delivered_order_paid_on_delivery_may_review_before_the_payout(
    product, settlement
):
    """Cash or card to the courier, or the carrier's payment link, is paid
    by the time the parcel is delivered; the order stays DELIVERED until
    the payout is reconciled, which is not the shopper's doing."""
    pay_way = PayWayFactory.create_offline_payment(settlement=settlement)
    client = _signed_in(
        bought=product, status=OrderStatus.DELIVERED, pay_way=pay_way
    )

    assert _review(client, product).status_code == 201


@pytest.mark.parametrize(
    "settlement", [PaySettlement.ONLINE, PaySettlement.OFFLINE_TRANSFER]
)
def test_a_delivered_order_not_yet_paid_cannot_review(product, settlement):
    pay_way = PayWayFactory.create_offline_payment(settlement=settlement)
    client = _signed_in(
        bought=product, status=OrderStatus.DELIVERED, pay_way=pay_way
    )

    assert _review(client, product).status_code == 400


@pytest.mark.parametrize(
    "status",
    [OrderStatus.SHIPPED, OrderStatus.CANCELED, OrderStatus.RETURNED],
)
def test_an_order_that_was_not_kept_cannot_review(product, status):
    pay_way = PayWayFactory.create_offline_payment(
        settlement=PaySettlement.COURIER_CASH
    )
    client = _signed_in(bought=product, status=status, pay_way=pay_way)

    assert _review(client, product).status_code == 400
