"""The cart's ``recommendations`` fields are served by the engine.

Both ``CartDetailSerializer`` and ``CartItemDetailSerializer`` keep the
field name and shape they always had — a plain list of serialized
products — so the OpenAPI contract did not move. What changed is the
source: an inline same-category query became
``recommendation.engine.suggest`` on the ``cart`` surface, which means
a merchant-curated relation now beats an inferred one, and the basket
as a whole is the seed for the cart-level field.

This replaces ``test_recommendations_cache.py``, which pinned the old
implementation's category-ID cache key and TTL. That cache no longer
exists: the Free-tier strategies run live (always fresh) and the
engine's own cache surface invalidates on write.
"""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.serializers.cart import CartDetailSerializer
from cart.serializers.item import CartItemDetailSerializer
from product.enum.relation import RelationType
from product.factories.product import ProductFactory
from product.models import ProductRelation
from tests.utils import TestURLFixerMixin
from user.factories.account import UserAccountFactory


def _sellable(**kwargs):
    # Fixed price: the cart slot drops candidates outside its price
    # band, and a random factory price would make every assertion here
    # a coin toss.
    kwargs.setdefault("stock", 5)
    return ProductFactory(
        active=True,
        price=Money(Decimal("50.00"), "EUR"),
        discount_percent=Decimal(0),
        num_images=0,
        num_reviews=0,
        **kwargs,
    )


class TestCartItemRecommendationsUseTheEngine(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.product = _sellable(stock=10)
        self.item = CartItemFactory(
            cart=self.cart, product=self.product, quantity=1
        )

    def _item_recommendations(self):
        serializer = CartItemDetailSerializer(
            self.item, context={"request": None}
        )
        return serializer.get_recommendations(self.item)

    def test_a_curated_relation_comes_first(self):
        """The whole point of the engine over the old category query."""
        curated = _sellable(category=self.product.category)
        ProductRelation.objects.create(
            from_product=self.product,
            to_product=curated,
            relation_type=RelationType.COMPLEMENTARY,
        )
        # A same-category product that is NOT curated.
        _sellable(category=self.product.category, view_count=10_000)

        ids = [row["id"] for row in self._item_recommendations()]

        assert ids, "expected recommendations"
        assert ids[0] == curated.id

    def test_the_line_itself_is_never_suggested(self):
        for _ in range(3):
            _sellable(category=self.product.category)

        ids = [row["id"] for row in self._item_recommendations()]

        assert ids
        assert self.product.id not in ids

    def test_out_of_stock_products_are_never_suggested(self):
        gone = _sellable(category=self.product.category, stock=0)
        _sellable(category=self.product.category)

        ids = [row["id"] for row in self._item_recommendations()]

        assert ids
        assert gone.id not in ids


class TestCartRecommendationsSeedWithTheBasket(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=0)

    def test_basket_lines_are_excluded_and_relations_of_any_line_count(self):
        first = _sellable()
        second = _sellable()
        CartItemFactory(cart=self.cart, product=first, quantity=1)
        CartItemFactory(cart=self.cart, product=second, quantity=1)
        companion = _sellable()
        ProductRelation.objects.create(
            from_product=second,
            to_product=companion,
            relation_type=RelationType.ACCESSORY,
        )

        data = CartDetailSerializer(
            self.cart, context={"request": None}
        ).get_recommendations(self.cart)
        ids = [row["id"] for row in data]

        assert companion.id in ids
        assert first.id not in ids
        assert second.id not in ids

    def test_an_empty_cart_has_no_recommendations(self):
        data = CartDetailSerializer(
            self.cart, context={"request": None}
        ).get_recommendations(self.cart)

        assert data == []


class TestCartItemDetailRecommendationsView(TestURLFixerMixin, APITestCase):
    """The wire contract: the field is present and is a list."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.cart = CartFactory(user=cls.user, num_cart_items=0)
        cls.product = _sellable(stock=10)
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
