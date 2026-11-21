from datetime import timedelta

import pytest
from django.utils import timezone

from cart.models import Cart
from cart.tasks import cleanup_abandoned_carts, cleanup_old_guest_carts
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestCleanupAbandonedCartsTask:
    def test_cleanup_abandoned_carts_removes_old_empty_carts(self):
        from cart.factories import CartItemFactory

        user = UserAccountFactory()
        user_cart = Cart.objects.create(user=user)

        old_empty_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=old_empty_cart.id).update(
            last_activity=timezone.now() - timedelta(days=10)
        )
        old_empty_cart.refresh_from_db()

        recent_empty_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=recent_empty_cart.id).update(
            last_activity=timezone.now() - timedelta(days=3)
        )
        recent_empty_cart.refresh_from_db()

        old_cart_with_items = Cart.objects.create(user=None)
        Cart.objects.filter(id=old_cart_with_items.id).update(
            last_activity=timezone.now() - timedelta(days=10)
        )
        old_cart_with_items.refresh_from_db()

        CartItemFactory(cart=old_cart_with_items)

        result = cleanup_abandoned_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 1

        assert not Cart.objects.filter(id=old_empty_cart.id).exists()

        assert Cart.objects.filter(id=recent_empty_cart.id).exists()

        assert Cart.objects.filter(id=user_cart.id).exists()

        assert Cart.objects.filter(id=old_cart_with_items.id).exists()

    def test_cleanup_abandoned_carts_no_carts_to_delete(self):
        result = cleanup_abandoned_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 0


@pytest.mark.django_db
class TestCleanupOldGuestCartsTask:
    def test_cleanup_old_guest_carts_removes_old_carts(self):
        user = UserAccountFactory()
        user_cart = Cart.objects.create(user=user)

        very_old_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=very_old_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )
        very_old_cart.refresh_from_db()

        recent_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=recent_cart.id).update(
            last_activity=timezone.now() - timedelta(days=15)
        )
        recent_cart.refresh_from_db()

        result = cleanup_old_guest_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 1

        assert not Cart.objects.filter(id=very_old_cart.id).exists()

        assert Cart.objects.filter(id=recent_cart.id).exists()

        assert Cart.objects.filter(id=user_cart.id).exists()

    def test_cleanup_old_guest_carts_removes_old_non_empty_carts(self):
        # Regression: a guest cart that still held items after abandonment
        # was never cleaned by anything (both jobs filtered
        # items__isnull=True), so it lingered forever. The 30-day job now
        # sweeps non-empty guest carts too.
        from cart.factories import CartItemFactory
        from product.factories.product import ProductFactory

        old_cart = Cart.objects.create(user=None)
        CartItemFactory(
            cart=old_cart,
            product=ProductFactory(num_images=0, num_reviews=0),
        )
        Cart.objects.filter(id=old_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )

        result = cleanup_old_guest_carts()

        assert result["status"] == "success"
        assert not Cart.objects.filter(id=old_cart.id).exists()

    def test_cleanup_old_guest_carts_no_carts_to_delete(self):
        result = cleanup_old_guest_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 0
