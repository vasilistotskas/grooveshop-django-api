"""Tests for R4-D2: CartItemDetailSerializer.get_recommendations cache.

Verifies that recommendations are served from the Django cache on the
second call and that the cache key is category-scoped.
"""

import pytest
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.serializers.item import CartItemDetailSerializer, _CART_RECS_TTL
from core.utils.testing import TestURLFixerMixin
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestCartRecommendationsCache(TestCase):
    """get_recommendations uses cache.get_or_set to avoid per-item queries."""

    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory(num_addresses=0)
        self.cart = CartFactory(user=self.user, num_cart_items=0)

        self.product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=10
        )
        self.cart_item = CartItemFactory(
            cart=self.cart, product=self.product, quantity=1
        )

    def _get_serializer_data(self):
        serializer = CartItemDetailSerializer(
            self.cart_item, context={"request": None}
        )
        # Only exercise get_recommendations directly to avoid hitting
        # get_weight_info which requires a measurement object on product.weight.
        return serializer.get_recommendations(self.cart_item)

    def test_recommendations_warm_cache_on_first_call(self):
        """First call populates the category cache key."""
        category = self.product.category
        if not category:
            pytest.skip("Product has no category")

        cache_key = f"cart_recs:cat:{category.pk}"
        cache.delete(cache_key)

        self._get_serializer_data()

        assert cache.get(cache_key) is not None

    def test_recommendations_use_cache_on_second_call(self):
        """Second call reads from cache — get_or_set is invoked with correct TTL."""
        category = self.product.category
        if not category:
            pytest.skip("Product has no category")

        cache_key = f"cart_recs:cat:{category.pk}"
        cache.delete(cache_key)

        # Warm the cache
        self._get_serializer_data()

        from unittest.mock import patch

        with patch(
            "cart.serializers.item.cache.get_or_set",
            wraps=cache.get_or_set,
        ) as mock_get_or_set:
            self._get_serializer_data()
            mock_get_or_set.assert_called_once()
            call_args = mock_get_or_set.call_args
            assert call_args[0][0] == cache_key
            assert call_args[0][2] == _CART_RECS_TTL

    def test_recommendations_no_category_returns_empty(self):
        """Product with no category returns empty list without a DB query."""
        from product.factories.product import ProductFactory

        no_cat_product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=5
        )
        # Remove the category from the product
        no_cat_product.category = None
        no_cat_product.save(update_fields=["category"])

        item = CartItemFactory(
            cart=self.cart, product=no_cat_product, quantity=1
        )
        serializer = CartItemDetailSerializer(item, context={"request": None})
        with self.assertNumQueries(0):
            result = serializer.get_recommendations(item)
        assert result == []


@pytest.mark.django_db
class TestCartItemDetailRecommendationsView(TestURLFixerMixin, APITestCase):
    """Detail view exposes recommendations via cached path."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.cart = CartFactory(user=cls.user, num_cart_items=0)
        cls.product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=10
        )
        cls.cart_item = CartItemFactory(
            cart=cls.cart, product=cls.product, quantity=1
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.detail_url = reverse(
            "cart-item-detail", kwargs={"pk": self.cart_item.pk}
        )

    def test_retrieve_includes_recommendations_field(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("recommendations", response.data)
        self.assertIsInstance(response.data["recommendations"], list)
