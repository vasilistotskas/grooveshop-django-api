from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
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
def guest_cart():
    return CartFactory(user=None, session_key="guest123")


@pytest.fixture
def user_cart(user):
    return CartFactory(user=user)


@pytest.fixture
def product():
    return ProductFactory()


@pytest.mark.django_db
class TestCartQuerySet:
    def test_active_carts_with_recent_activity(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(hours=1)
        )

        active_carts = Cart.objects.active()
        assert user_cart in active_carts

    def test_active_carts_excludes_empty_carts(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now()
        )

        active_carts = Cart.objects.active()
        assert user_cart not in active_carts

    @patch("extra_settings.models.Setting.get")
    def test_active_carts_with_custom_threshold(
        self, mock_setting, user_cart, product
    ):
        mock_setting.return_value = 48
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(hours=25)
        )

        active_carts = Cart.objects.active()
        assert user_cart in active_carts

    def test_abandoned_carts(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(hours=25)
        )

        abandoned_carts = Cart.objects.abandoned()
        assert user_cart in abandoned_carts

    @patch("extra_settings.models.Setting.get")
    def test_abandoned_carts_with_custom_threshold(
        self, mock_setting, user_cart, product
    ):
        mock_setting.return_value = 12
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(hours=15)
        )

        abandoned_carts = Cart.objects.abandoned()
        assert user_cart in abandoned_carts

    def test_empty_carts(self, user_cart):
        empty_carts = Cart.objects.empty()
        assert user_cart in empty_carts

    def test_with_items(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)

        carts_with_items = Cart.objects.with_items()
        assert user_cart in carts_with_items

    def test_for_authenticated_user(self, user, user_cart):
        user_carts = Cart.objects.for_user(user)
        assert user_cart in user_carts

    def test_for_anonymous_user(self):
        anonymous_user = AnonymousUser()

        user_carts = Cart.objects.for_user(anonymous_user)
        assert user_carts.count() == 0

    def test_guest_carts(self, guest_cart, user_cart):
        guest_carts = Cart.objects.guest_carts()
        assert guest_cart in guest_carts
        assert user_cart not in guest_carts

    def test_user_carts(self, guest_cart, user_cart):
        user_carts = Cart.objects.user_carts()
        assert user_cart in user_carts
        assert guest_cart not in user_carts

    def test_with_totals(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product, quantity=3)
        CartItemFactory(cart=user_cart, product=ProductFactory(), quantity=2)

        carts_with_totals = Cart.objects.with_totals()
        cart = carts_with_totals.get(id=user_cart.id)

        assert cart.total_quantity == 5
        assert cart.unique_items_count == 2

    def test_expired_carts_default_30_days(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )

        expired_carts = Cart.objects.expired()
        assert user_cart in expired_carts

    def test_expired_carts_custom_days(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=8)
        )

        expired_carts = Cart.objects.expired(days=7)
        assert user_cart in expired_carts

    def test_by_date_range(self, user_cart):
        user_cart.created_at = timezone.now() - timedelta(days=5)
        user_cart.save()

        start_date = (timezone.now() - timedelta(days=7)).date()
        end_date = (timezone.now() - timedelta(days=3)).date()

        carts_in_range = Cart.objects.by_date_range(start_date, end_date)
        assert user_cart in carts_in_range

    def test_recent_carts_default_7_days(self, user_cart):
        user_cart.created_at = timezone.now() - timedelta(days=3)
        user_cart.save()

        recent_carts = Cart.objects.recent()
        assert user_cart in recent_carts

    def test_recent_carts_custom_days(self, user_cart):
        user_cart.created_at = timezone.now() - timedelta(days=2)
        user_cart.save()

        recent_carts = Cart.objects.recent(days=3)
        assert user_cart in recent_carts

    def test_by_country(self, user_cart, country):
        carts_by_country = Cart.objects.by_country("US")
        assert user_cart in carts_by_country

    def test_with_specific_product(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)

        carts_with_product = Cart.objects.with_specific_product(product)
        assert user_cart in carts_with_product


@pytest.mark.django_db
class TestCartManager:
    def test_manager_delegates_to_queryset_active(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now()
        )

        active_carts = Cart.objects.active()
        assert user_cart in active_carts

    def test_manager_delegates_to_queryset_abandoned(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(hours=25)
        )

        abandoned_carts = Cart.objects.abandoned()
        assert user_cart in abandoned_carts

    def test_manager_delegates_to_queryset_empty(self, user_cart):
        empty_carts = Cart.objects.empty()
        assert user_cart in empty_carts

    def test_manager_delegates_to_queryset_with_items(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product)

        carts_with_items = Cart.objects.with_items()
        assert user_cart in carts_with_items

    def test_manager_delegates_to_queryset_for_user(self, user, user_cart):
        user_carts = Cart.objects.for_user(user)
        assert user_cart in user_carts

    def test_manager_delegates_to_queryset_guest_carts(self, guest_cart):
        guest_carts = Cart.objects.guest_carts()
        assert guest_cart in guest_carts

    def test_manager_delegates_to_queryset_user_carts(self, user_cart):
        user_carts = Cart.objects.user_carts()
        assert user_cart in user_carts

    def test_manager_delegates_to_queryset_with_totals(
        self, user_cart, product
    ):
        CartItemFactory(cart=user_cart, product=product, quantity=2)

        carts_with_totals = Cart.objects.with_totals()
        cart = carts_with_totals.get(id=user_cart.id)
        assert hasattr(cart, "total_quantity")

    def test_manager_delegates_to_queryset_expired(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )

        expired_carts = Cart.objects.expired()
        assert user_cart in expired_carts

    def test_manager_delegates_to_queryset_by_date_range(self, user_cart):
        user_cart.created_at = timezone.now() - timedelta(days=5)
        user_cart.save()

        start_date = (timezone.now() - timedelta(days=7)).date()
        end_date = (timezone.now() - timedelta(days=3)).date()

        carts_in_range = Cart.objects.by_date_range(start_date, end_date)
        assert user_cart in carts_in_range

    def test_manager_delegates_to_queryset_recent(self, user_cart):
        user_cart.created_at = timezone.now() - timedelta(days=3)
        user_cart.save()

        recent_carts = Cart.objects.recent()
        assert user_cart in recent_carts

    def test_manager_delegates_to_queryset_by_country(self, user_cart):
        carts_by_country = Cart.objects.by_country("US")
        assert user_cart in carts_by_country

    def test_manager_delegates_to_queryset_with_specific_product(
        self, user_cart, product
    ):
        CartItemFactory(cart=user_cart, product=product)

        carts_with_product = Cart.objects.with_specific_product(product)
        assert user_cart in carts_with_product

    def test_cleanup_expired_returns_count(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )

        count = Cart.objects.cleanup_expired()
        assert count == 1

    def test_cleanup_expired_deletes_carts(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )
        cart_id = user_cart.id

        Cart.objects.cleanup_expired()

        with pytest.raises(Cart.DoesNotExist):
            Cart.objects.get(id=cart_id)

    def test_cleanup_expired_custom_days(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=8)
        )

        count = Cart.objects.cleanup_expired(days=7)
        assert count == 1

    def test_cleanup_expired_preserves_recent_carts(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=5)
        )

        count = Cart.objects.cleanup_expired()
        assert count == 0
        assert Cart.objects.filter(id=user_cart.id).exists()


@pytest.mark.django_db
class TestCartQuerySetEdgeCases:
    def test_active_carts_with_no_items_excluded(self):
        cart = CartFactory()
        Cart.objects.filter(id=cart.id).update(last_activity=timezone.now())

        active_carts = Cart.objects.active()
        assert cart not in active_carts

    def test_by_country_with_no_user(self, guest_cart):
        carts_by_country = Cart.objects.by_country("US")
        assert guest_cart not in carts_by_country

    def test_with_specific_product_distinct_results(self, user_cart, product):
        CartItemFactory(cart=user_cart, product=product, quantity=1)
        CartItemFactory(cart=user_cart, product=product, quantity=2)

        carts_with_product = Cart.objects.with_specific_product(product)
        assert carts_with_product.count() == 1

    def test_expired_carts_boundary_condition(self, user_cart):
        Cart.objects.filter(id=user_cart.id).update(
            last_activity=timezone.now() - timedelta(days=30, seconds=1)
        )

        expired_carts = Cart.objects.expired(days=30)
        assert user_cart in expired_carts
