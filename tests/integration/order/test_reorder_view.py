"""``POST /api/v1/order/{id}/reorder`` (``OrderViewSet.reorder``,
``order/views/order.py``) and ``OrderService.reorder_to_cart``
(``order/services.py``).

A past order's lines are copied into the caller's own cart. Inactive and
out-of-stock products are reported in ``skipped_items``; a line with
less stock than was ordered is added at the stock level and reported in
BOTH lists with reason ``partial``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from cart.models import Cart, CartItem
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _url(order) -> str:
    return reverse("order-reorder", kwargs={"pk": order.pk})


def _client(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _order_with(user, *lines):
    """An order for *user* holding ``(product, quantity)`` lines.

    Unpaid on purpose: a paid order fires ``order_paid``, which clears the
    user's cart, so a random factory state would delete the cart a test
    set up before calling this."""
    order = OrderFactory(
        user=user,
        num_order_items=0,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
    )
    for product, quantity in lines:
        order.items.create(
            product=product,
            price=Money(Decimal("10.00"), "EUR"),
            quantity=quantity,
        )
    return order


class TestReorderAccess:
    def test_anonymous_caller_is_401(self):
        order = _order_with(
            UserAccountFactory(), (ProductFactory(stock=5, active=True), 1)
        )

        response = _client().post(_url(order))

        assert response.status_code == 401
        assert not CartItem.objects.exists()

    def test_another_users_order_is_forbidden(self):
        order = _order_with(
            UserAccountFactory(), (ProductFactory(stock=5, active=True), 1)
        )
        intruder = UserAccountFactory()

        response = _client(intruder).post(_url(order))

        assert response.status_code == 403
        assert not Cart.objects.filter(user=intruder).exists()
        assert not CartItem.objects.exists()

    def test_a_guest_order_cannot_be_reordered_by_a_signed_in_user(self):
        order = _order_with(None, (ProductFactory(stock=5, active=True), 1))
        user = UserAccountFactory()

        response = _client(user).post(_url(order))

        assert response.status_code == 403
        assert not CartItem.objects.exists()


class TestReorderToCart:
    def test_every_line_in_stock_is_added_to_the_users_cart(self):
        user = UserAccountFactory()
        first = ProductFactory(stock=10, active=True)
        second = ProductFactory(stock=10, active=True)
        order = _order_with(user, (first, 2), (second, 3))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        cart = Cart.objects.get(user=user)
        assert response.data["cart_id"] == cart.id
        assert response.data["skipped_items"] == []
        assert sorted(
            (item["product_id"], item["added_quantity"])
            for item in response.data["added_items"]
        ) == sorted([(first.id, 2), (second.id, 3)])
        assert dict(cart.items.values_list("product_id", "quantity")) == {
            first.id: 2,
            second.id: 3,
        }

    def test_inactive_product_is_skipped(self):
        user = UserAccountFactory()
        inactive = ProductFactory(stock=10, active=False)
        order = _order_with(user, (inactive, 2))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert response.data["added_items"] == []
        assert response.data["skipped_items"] == [
            {
                "product_id": inactive.id,
                "requested_quantity": 2,
                "added_quantity": 0,
                "reason": "inactive",
            }
        ]
        assert not CartItem.objects.filter(product=inactive).exists()

    def test_out_of_stock_product_is_skipped(self):
        user = UserAccountFactory()
        sold_out = ProductFactory(stock=0, active=True)
        order = _order_with(user, (sold_out, 1))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert response.data["added_items"] == []
        assert response.data["skipped_items"] == [
            {
                "product_id": sold_out.id,
                "requested_quantity": 1,
                "added_quantity": 0,
                "reason": "out_of_stock",
            }
        ]
        assert not CartItem.objects.filter(product=sold_out).exists()

    def test_short_stock_adds_what_is_left_and_reports_it_as_partial(self):
        user = UserAccountFactory()
        scarce = ProductFactory(stock=2, active=True)
        order = _order_with(user, (scarce, 5))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        partial = {
            "product_id": scarce.id,
            "requested_quantity": 5,
            "added_quantity": 2,
            "reason": "partial",
        }
        assert response.data["added_items"] == [partial]
        assert response.data["skipped_items"] == [partial]
        assert CartItem.objects.get(product=scarce).quantity == 2

    def test_mixed_order_adds_available_lines_and_skips_the_rest(self):
        user = UserAccountFactory()
        available = ProductFactory(stock=10, active=True)
        inactive = ProductFactory(stock=10, active=False)
        order = _order_with(user, (available, 1), (inactive, 1))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert [i["product_id"] for i in response.data["added_items"]] == [
            available.id
        ]
        assert [i["product_id"] for i in response.data["skipped_items"]] == [
            inactive.id
        ]
        assert list(
            Cart.objects.get(user=user).items.values_list(
                "product_id", flat=True
            )
        ) == [available.id]

    def test_existing_cart_line_is_merged_not_duplicated(self):
        user = UserAccountFactory()
        product = ProductFactory(stock=10, active=True)
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)
        order = _order_with(user, (product, 2))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert response.data["cart_id"] == cart.id
        line = CartItem.objects.get(cart=cart, product=product)
        assert line.quantity == 3
        assert CartItem.objects.filter(cart=cart).count() == 1

    def test_the_cart_is_locked_before_any_line_is_read(self):
        """Two reorders racing for one user must run one after the other:
        each reads "no line yet" otherwise, and the second insert breaks
        the cart's one-line-per-product constraint."""
        user = UserAccountFactory()
        order = _order_with(user, (ProductFactory(stock=5, active=True), 1))
        cart_table = Cart._meta.db_table
        line_table = CartItem._meta.db_table

        with CaptureQueriesContext(connection) as queries:
            response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        cart_id = response.data["cart_id"]
        sql = [query["sql"] for query in queries.captured_queries]
        cart_lock = next(
            (
                i
                for i, q in enumerate(sql)
                if f'FROM "{cart_table}"' in q
                and "FOR UPDATE" in q
                and f'"{cart_table}"."id" = {cart_id}' in q
            ),
            None,
        )
        assert cart_lock is not None, "\n".join(sql)
        first_line_read = next(
            i for i, q in enumerate(sql) if f'FROM "{line_table}"' in q
        )
        assert cart_lock < first_line_read

    def test_merged_line_is_capped_at_stock_and_reported_partial(self):
        user = UserAccountFactory()
        product = ProductFactory(stock=3, active=True)
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, product=product, quantity=2)
        order = _order_with(user, (product, 3))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        partial = {
            "product_id": product.id,
            "requested_quantity": 3,
            "added_quantity": 1,
            "reason": "partial",
        }
        assert response.data["added_items"] == [partial]
        assert response.data["skipped_items"] == [partial]
        assert CartItem.objects.get(cart=cart, product=product).quantity == 3

    def test_line_already_holding_all_stock_adds_nothing(self):
        user = UserAccountFactory()
        product = ProductFactory(stock=3, active=True)
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, product=product, quantity=3)
        order = _order_with(user, (product, 2))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert response.data["added_items"] == []
        assert response.data["skipped_items"] == [
            {
                "product_id": product.id,
                "requested_quantity": 2,
                "added_quantity": 0,
                "reason": "partial",
            }
        ]
        assert CartItem.objects.get(cart=cart, product=product).quantity == 3

    def test_reorder_never_touches_another_users_cart(self):
        user = UserAccountFactory()
        product = ProductFactory(stock=10, active=True)
        bystander_cart = Cart.objects.create(user=UserAccountFactory())
        order = _order_with(user, (product, 1))

        response = _client(user).post(_url(order))

        assert response.status_code == 200, response.data
        assert response.data["cart_id"] != bystander_cart.id
        assert not bystander_cart.items.exists()
