from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.utils import timezone
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart, CartItem
from country.factories import CountryFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

User = get_user_model()


@pytest.fixture
def country():
    return CountryFactory(alpha_2="US", name="United States")


@pytest.fixture
def user(country):
    return UserAccountFactory(country=country)


@pytest.fixture
def cart(user):
    return CartFactory(user=user)


@pytest.fixture
def guest_cart():
    return CartFactory(user=None, session_key="guest123")


@pytest.fixture
def product():
    return ProductFactory(price=Money(50, "USD"))


@pytest.fixture
def expensive_product():
    return ProductFactory(price=Money(150, "USD"))


@pytest.fixture
def discounted_product():
    return ProductFactory(price=Money(30, "USD"), discount_percent=20)


@pytest.mark.django_db
class TestCartItemQuerySet:
    def test_for_cart(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)
        other_cart = CartFactory()
        CartItemFactory(cart=other_cart, product=product)

        items_for_cart = CartItem.objects.for_cart(cart)
        assert item in items_for_cart
        assert items_for_cart.count() == 1

    def test_for_product(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)
        other_product = ProductFactory()
        CartItemFactory(cart=cart, product=other_product)

        items_for_product = CartItem.objects.for_product(product)
        assert item in items_for_product
        assert items_for_product.count() == 1

    def test_for_authenticated_user(self, user, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_for_user = CartItem.objects.for_user(user)
        assert item in items_for_user

    def test_for_anonymous_user(self):
        anonymous_user = AnonymousUser()

        items_for_user = CartItem.objects.for_user(anonymous_user)
        assert items_for_user.count() == 0

    def test_with_product_data(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_with_data = CartItem.objects.with_product_data()
        item_with_data = items_with_data.get(id=item.id)

        assert hasattr(item_with_data, "product")
        assert hasattr(item_with_data, "cart")

    def test_total_quantity(self, cart, product):
        CartItemFactory(cart=cart, product=product, quantity=3)
        CartItemFactory(cart=cart, product=ProductFactory(), quantity=5)

        total = CartItem.objects.total_quantity()
        assert total == 8

    def test_total_quantity_empty(self):
        total = CartItem.objects.total_quantity()
        assert total == 0

    def test_by_product_popularity(self, cart, product):
        _ = CartItemFactory(cart=cart, product=product, quantity=5)
        cart2 = CartFactory(user=None, session_key="guest456")
        _ = CartItemFactory(cart=cart2, product=product, quantity=3)

        other_product = ProductFactory()
        CartItemFactory(cart=cart, product=other_product, quantity=2)

        popularity_data = CartItem.objects.by_product_popularity()

        first_item = popularity_data.first()
        assert first_item["product"] == product.id
        assert first_item["total_quantity"] == 8
        assert first_item["cart_count"] == 2

    def test_high_quantity_default_threshold(self, cart, product):
        high_quantity_item = CartItemFactory(
            cart=cart, product=product, quantity=6
        )
        low_quantity_item = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=3
        )

        high_quantity_items = CartItem.objects.high_quantity()
        assert high_quantity_item in high_quantity_items
        assert low_quantity_item not in high_quantity_items

    def test_high_quantity_custom_threshold(self, cart, product):
        item = CartItemFactory(cart=cart, product=product, quantity=8)

        high_quantity_items = CartItem.objects.high_quantity(threshold=10)
        assert item not in high_quantity_items

        high_quantity_items = CartItem.objects.high_quantity(threshold=7)
        assert item in high_quantity_items

    def test_recent_items_default_7_days(self, cart, product):
        recent_item = CartItemFactory(cart=cart, product=product)
        recent_item.created_at = timezone.now() - timedelta(days=3)
        recent_item.save()

        old_item = CartItemFactory(cart=cart, product=ProductFactory())
        old_item.created_at = timezone.now() - timedelta(days=10)
        old_item.save()

        recent_items = CartItem.objects.recent()
        assert recent_item in recent_items
        assert old_item not in recent_items

    def test_recent_items_custom_days(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)
        item.created_at = timezone.now() - timedelta(days=5)
        item.save()

        recent_items = CartItem.objects.recent(days=3)
        assert item not in recent_items

        recent_items = CartItem.objects.recent(days=7)
        assert item in recent_items

    def test_by_date_range(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)
        item.created_at = timezone.now() - timedelta(days=5)
        item.save()

        start_date = (timezone.now() - timedelta(days=7)).date()
        end_date = (timezone.now() - timedelta(days=3)).date()

        items_in_range = CartItem.objects.by_date_range(start_date, end_date)
        assert item in items_in_range

    def test_by_quantity_range(self, cart, product):
        low_item = CartItemFactory(cart=cart, product=product, quantity=2)
        mid_item = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=5
        )
        high_item = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=8
        )

        items_in_range = CartItem.objects.by_quantity_range(3, 6)
        assert low_item not in items_in_range
        assert mid_item in items_in_range
        assert high_item not in items_in_range

    def test_by_price_range(self, cart):
        cheap_product = ProductFactory(price=Money(20, "USD"))
        mid_product = ProductFactory(price=Money(75, "USD"))
        expensive_product = ProductFactory(price=Money(200, "USD"))

        cheap_item = CartItemFactory(cart=cart, product=cheap_product)
        mid_item = CartItemFactory(cart=cart, product=mid_product)
        expensive_item = CartItemFactory(cart=cart, product=expensive_product)

        items_in_range = CartItem.objects.by_price_range(50, 100)
        assert cheap_item not in items_in_range
        assert mid_item in items_in_range
        assert expensive_item not in items_in_range

    def test_expensive_items_default_threshold(
        self, cart, product, expensive_product
    ):
        regular_item = CartItemFactory(cart=cart, product=product)
        expensive_item = CartItemFactory(cart=cart, product=expensive_product)

        expensive_items = CartItem.objects.expensive_items()
        assert regular_item not in expensive_items
        assert expensive_item in expensive_items

    def test_expensive_items_custom_threshold(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        expensive_items = CartItem.objects.expensive_items(threshold=30)
        assert item in expensive_items

        expensive_items = CartItem.objects.expensive_items(threshold=80)
        assert item not in expensive_items

    @patch("extra_settings.models.Setting.get")
    def test_in_active_carts(self, mock_setting, cart, product):
        mock_setting.return_value = 24

        active_item = CartItemFactory(cart=cart, product=product)

        abandoned_cart = CartFactory(user=None, session_key="guest_abandoned")
        abandoned_item = CartItemFactory(
            cart=abandoned_cart, product=ProductFactory()
        )

        active_time = timezone.now() - timedelta(hours=1)
        abandoned_time = timezone.now() - timedelta(hours=25)

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE cart_cart SET last_activity = %s WHERE id = %s",
                [active_time, cart.id],
            )
            cursor.execute(
                "UPDATE cart_cart SET last_activity = %s WHERE id = %s",
                [abandoned_time, abandoned_cart.id],
            )

        cart.refresh_from_db()
        abandoned_cart.refresh_from_db()

        active_items = CartItem.objects.in_active_carts()

        assert active_item in active_items
        assert abandoned_item not in active_items

    @patch("extra_settings.models.Setting.get")
    def test_in_abandoned_carts(self, mock_setting, cart, product):
        mock_setting.return_value = 24

        abandoned_item = CartItemFactory(cart=cart, product=product)

        active_cart = CartFactory(user=None, session_key="guest_active")
        active_item = CartItemFactory(
            cart=active_cart, product=ProductFactory()
        )

        abandoned_time = timezone.now() - timedelta(hours=25)
        active_time = timezone.now() - timedelta(hours=1)

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE cart_cart SET last_activity = %s WHERE id = %s",
                [abandoned_time, cart.id],
            )
            cursor.execute(
                "UPDATE cart_cart SET last_activity = %s WHERE id = %s",
                [active_time, active_cart.id],
            )

        cart.refresh_from_db()
        active_cart.refresh_from_db()

        abandoned_items = CartItem.objects.in_abandoned_carts()
        assert abandoned_item in abandoned_items
        assert active_item not in abandoned_items

    def test_with_discounts(self, cart):
        regular_product = ProductFactory(discount_percent=0)
        discounted_product = ProductFactory(discount_percent=20)

        regular_item = CartItemFactory(cart=cart, product=regular_product)
        discounted_item = CartItemFactory(cart=cart, product=discounted_product)

        items_with_discounts = CartItem.objects.with_discounts()
        assert regular_item not in items_with_discounts
        assert discounted_item in items_with_discounts


@pytest.mark.django_db
class TestCartItemManager:
    def test_manager_delegates_to_queryset_for_cart(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_for_cart = CartItem.objects.for_cart(cart)
        assert item in items_for_cart

    def test_manager_delegates_to_queryset_for_product(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_for_product = CartItem.objects.for_product(product)
        assert item in items_for_product

    def test_manager_delegates_to_queryset_for_user(self, user, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_for_user = CartItem.objects.for_user(user)
        assert item in items_for_user

    def test_manager_delegates_to_queryset_with_product_data(
        self, cart, product
    ):
        _ = CartItemFactory(cart=cart, product=product)

        items_with_data = CartItem.objects.with_product_data()
        assert items_with_data.count() == 1

    def test_manager_delegates_to_queryset_total_quantity(self, cart, product):
        CartItemFactory(cart=cart, product=product, quantity=3)

        total = CartItem.objects.total_quantity()
        assert total == 3

    def test_manager_delegates_to_queryset_by_product_popularity(
        self, cart, product
    ):
        CartItemFactory(cart=cart, product=product, quantity=5)

        popularity_data = CartItem.objects.by_product_popularity()
        assert popularity_data.count() == 1

    def test_manager_delegates_to_queryset_high_quantity(self, cart, product):
        item = CartItemFactory(cart=cart, product=product, quantity=6)

        high_quantity_items = CartItem.objects.high_quantity()
        assert item in high_quantity_items

    def test_manager_delegates_to_queryset_recent(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        recent_items = CartItem.objects.recent()
        assert item in recent_items

    def test_manager_delegates_to_queryset_by_date_range(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)
        item.created_at = timezone.now() - timedelta(days=5)
        item.save()

        start_date = (timezone.now() - timedelta(days=7)).date()
        end_date = (timezone.now() - timedelta(days=3)).date()

        items_in_range = CartItem.objects.by_date_range(start_date, end_date)
        assert item in items_in_range

    def test_manager_delegates_to_queryset_by_quantity_range(
        self, cart, product
    ):
        item = CartItemFactory(cart=cart, product=product, quantity=5)

        items_in_range = CartItem.objects.by_quantity_range(3, 6)
        assert item in items_in_range

    def test_manager_delegates_to_queryset_by_price_range(self, cart, product):
        item = CartItemFactory(cart=cart, product=product)

        items_in_range = CartItem.objects.by_price_range(40, 60)
        assert item in items_in_range

    def test_manager_delegates_to_queryset_expensive_items(
        self, cart, expensive_product
    ):
        item = CartItemFactory(cart=cart, product=expensive_product)

        expensive_items = CartItem.objects.expensive_items()
        assert item in expensive_items

    def test_manager_delegates_to_queryset_in_active_carts(self, cart, product):
        Cart.objects.filter(id=cart.id).update(last_activity=timezone.now())
        item = CartItemFactory(cart=cart, product=product)

        active_items = CartItem.objects.in_active_carts()
        assert item in active_items

    def test_manager_delegates_to_queryset_in_abandoned_carts(
        self, cart, product
    ):
        Cart.objects.filter(id=cart.id).update(
            last_activity=timezone.now() - timedelta(hours=25)
        )
        item = CartItemFactory(cart=cart, product=product)

        abandoned_items = CartItem.objects.in_abandoned_carts()
        assert item in abandoned_items

    def test_manager_delegates_to_queryset_with_discounts(
        self, cart, discounted_product
    ):
        item = CartItemFactory(cart=cart, product=discounted_product)

        items_with_discounts = CartItem.objects.with_discounts()
        assert item in items_with_discounts


@pytest.mark.django_db
class TestCartItemQuerySetEdgeCases:
    def test_by_product_popularity_with_no_items(self):
        popularity_data = CartItem.objects.by_product_popularity()
        assert popularity_data.count() == 0

    def test_total_quantity_aggregation_accuracy(self, cart):
        CartItemFactory(cart=cart, product=ProductFactory(), quantity=3)
        CartItemFactory(cart=cart, product=ProductFactory(), quantity=7)
        CartItemFactory(cart=cart, product=ProductFactory(), quantity=2)

        total = CartItem.objects.total_quantity()
        assert total == 12

    def test_price_range_boundary_conditions(self, cart):
        product_at_min = ProductFactory(price=Money(50, "USD"))
        product_at_max = ProductFactory(price=Money(100, "USD"))
        product_below = ProductFactory(price=Money(49.99, "USD"))
        product_above = ProductFactory(price=Money(100.01, "USD"))

        item_at_min = CartItemFactory(cart=cart, product=product_at_min)
        item_at_max = CartItemFactory(cart=cart, product=product_at_max)
        item_below = CartItemFactory(cart=cart, product=product_below)
        item_above = CartItemFactory(cart=cart, product=product_above)

        items_in_range = CartItem.objects.by_price_range(50, 100)
        assert item_at_min in items_in_range
        assert item_at_max in items_in_range
        assert item_below not in items_in_range
        assert item_above not in items_in_range

    def test_quantity_range_boundary_conditions(self, cart, product):
        item_at_min = CartItemFactory(cart=cart, product=product, quantity=5)
        item_at_max = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=10
        )
        item_below = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=4
        )
        item_above = CartItemFactory(
            cart=cart, product=ProductFactory(), quantity=11
        )

        items_in_range = CartItem.objects.by_quantity_range(5, 10)
        assert item_at_min in items_in_range
        assert item_at_max in items_in_range
        assert item_below not in items_in_range
        assert item_above not in items_in_range

    def test_with_discounts_zero_discount_excluded(self, cart):
        zero_discount_product = ProductFactory(discount_percent=0)
        item = CartItemFactory(cart=cart, product=zero_discount_product)

        items_with_discounts = CartItem.objects.with_discounts()
        assert item not in items_with_discounts
