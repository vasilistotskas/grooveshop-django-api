"""The delivered and completed emails invite a review of each product.

The link opens the storefront's review form (``#write-review``). It is
offered only where the shopper can use it: the store has product reviews
on, and, when a purchase is required to review, the order belongs to an
account (a guest order is never a verified purchase).
"""

from __future__ import annotations

import pytest
from django.core import mail

from order.emails import REVIEW_FORM_FRAGMENT
from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.tasks import send_order_status_update_email
from product.factories.product import ProductFactory
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

REVIEWING_STATUSES = [OrderStatus.DELIVERED, OrderStatus.COMPLETED]


def _send(status, *, user, products) -> str:
    order = OrderFactory(
        status=status,
        user=user,
        email="customer@example.test",
        language_code="en",
        num_order_items=0,
    )
    for product in products:
        OrderItemFactory(order=order, product=product)
    mail.outbox.clear()

    assert send_order_status_update_email.run(order.id, status) is True

    [message] = mail.outbox
    [(html, _mimetype)] = message.alternatives
    return f"{message.body}\n{html}"


@pytest.fixture
def products():
    return [
        ProductFactory(num_images=0, num_reviews=0, slug="first-product"),
        ProductFactory(num_images=0, num_reviews=0, slug="second-product"),
    ]


@pytest.mark.usefixtures("tenant")
@pytest.mark.parametrize("status", REVIEWING_STATUSES)
class TestReviewLinks:
    def test_each_product_links_to_its_review_form(self, status, products):
        body = _send(status, user=UserAccountFactory(), products=products)

        for product in products:
            assert (
                f"https://shop.example/en/products/{product.id}/"
                f"{product.slug}{REVIEW_FORM_FRAGMENT}"
            ) in body

    def test_reviews_off_leaves_no_link(self, status, products):
        switch_store_settings(PRODUCT_REVIEWS_ENABLED=False)

        body = _send(status, user=UserAccountFactory(), products=products)

        assert REVIEW_FORM_FRAGMENT not in body

    def test_a_guest_order_gets_no_link_when_a_purchase_is_required(
        self, status, products
    ):
        switch_store_settings(PRODUCT_REVIEWS_PURCHASE_REQUIRED=True)

        body = _send(status, user=None, products=products)

        assert REVIEW_FORM_FRAGMENT not in body

    def test_a_guest_order_gets_the_link_when_any_signed_in_user_may_review(
        self, status, products
    ):
        switch_store_settings(PRODUCT_REVIEWS_PURCHASE_REQUIRED=False)

        body = _send(status, user=None, products=products)

        assert body.count(REVIEW_FORM_FRAGMENT) >= len(products)


@pytest.mark.usefixtures("tenant")
@pytest.mark.parametrize("status", [OrderStatus.CANCELED, OrderStatus.RETURNED])
def test_other_status_emails_carry_no_review_link(status, products):
    body = _send(status, user=UserAccountFactory(), products=products)

    assert REVIEW_FORM_FRAGMENT not in body
