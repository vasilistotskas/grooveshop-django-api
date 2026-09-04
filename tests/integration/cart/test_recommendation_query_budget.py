"""Recommendations must not cost a query per recommended product.

Both recommendation fields fed a BARE `Product.objects.filter(...)` into
`ProductSerializer`, which renders translations, the main image, review
counts and like counts. Without `for_list()`'s prefetches every one of
those falls back to a per-instance query.

The existing `test_retrieve_no_n_plus_one` cannot see this: it varies the
number of CART ITEMS, and the recommendation set is capped independently
of cart size, so the cost it adds is constant under that test and
invisible. These vary the number of RECOMMENDATIONS instead.

`CartItemDetailSerializer` matters more than it looks: it is the response
serializer for create, retrieve, update and partial_update, so every
add-to-cart and every quantity change paid this too.
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from product.factories.product import ProductFactory
from tests.utils import TestURLFixerMixin, count_queries
from user.factories.account import UserAccountFactory


class CartRecommendationQueryBudgetTest(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.anchor = ProductFactory(active=True, num_images=0, num_reviews=0)
        CartItemFactory(cart=self.cart, product=self.anchor, quantity=1)
        self.category = self.anchor.category

    def _add_recommendable(self, count):
        for _ in range(count):
            ProductFactory(
                active=True,
                category=self.category,
                num_images=0,
                num_reviews=0,
            )

    def test_cart_detail_cost_does_not_grow_with_recommendations(self):
        url = reverse("cart-detail")
        self._add_recommendable(1)
        with count_queries() as few:
            self.client.get(url)

        self._add_recommendable(3)
        with count_queries() as many:
            self.client.get(url)

        assert many.count == few.count, (
            f"cart detail cost grew from {few.count} to {many.count} "
            f"queries when three more recommendable products existed — "
            f"the recommendation queryset is missing for_list()"
        )

    def test_cart_item_cost_does_not_grow_with_recommendations(self):
        from django.core.cache import cache

        item = self.cart.items.first()
        url = reverse("cart-item-detail", args=[item.id])

        # The category -> product-ID list is cached, so without clearing
        # it the second call reuses the FIRST list and the growth is
        # hidden. Serialization happens outside that cache, which is
        # exactly where the per-product cost lives.
        self._add_recommendable(1)
        cache.clear()
        response = self.client.get(url)
        assert response.status_code == 200, response.status_code
        assert response.data["recommendations"], "no recommendations to cost"
        with count_queries() as few:
            cache.clear()
            self.client.get(url)

        self._add_recommendable(2)
        with count_queries() as many:
            cache.clear()
            self.client.get(url)

        assert many.count == few.count, (
            f"cart-item detail cost grew from {few.count} to "
            f"{many.count} queries — this serializer is also the "
            f"add-to-cart and quantity-change response"
        )


class CartPromotionCodeHelper:
    @staticmethod
    def attach(cart, count=1):
        from promotion.factories.promotion import (
            PromotionCodeFactory,
            PromotionFactory,
        )
        from promotion.models.cart_code import CartPromotionCode

        promotion = PromotionFactory()
        for _ in range(count):
            CartPromotionCode.objects.create(
                cart=cart, code=PromotionCodeFactory(promotion=promotion)
            )


class CartCouponPrefetchBudgetTest(TestURLFixerMixin, APITestCase):
    """`get_applied_coupon_codes` was not prefetched.

    It walks `obj.applied_codes` and follows `code__code`, and neither
    `for_list()` nor `for_detail()` covered either hop — so every coupon
    on a cart cost two queries on every render.

    This varies the number of CODES rather than the number of carts:
    the cart list also runs the promotion engine per row, which is a
    separate finding, and measuring cart count would conflate the two.
    """

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=1)

    def _attach_codes(self, count):
        from promotion.factories.promotion import (
            PromotionCodeFactory,
            PromotionFactory,
        )
        from promotion.models.cart_code import CartPromotionCode

        promotion = PromotionFactory()
        for _ in range(count):
            CartPromotionCode.objects.create(
                cart=self.cart,
                code=PromotionCodeFactory(promotion=promotion),
            )

    def test_the_prefetch_is_actually_used(self):
        """Measured on the queryset, not the endpoint.

        The cart LIST also runs the promotion engine per row — a
        separate finding — so an endpoint measurement would conflate the
        two and could pass for the wrong reason.
        """
        from cart.models.cart import Cart

        self._attach_codes(2)
        for _ in range(3):
            other = CartFactory(user=UserAccountFactory(num_addresses=0))
            CartPromotionCodeHelper.attach(other)

        carts = list(Cart.objects.for_list())
        assert len(carts) >= 4

        with count_queries() as counter:
            for cart in carts:
                [row.code.code for row in cart.applied_codes.all()]

        assert counter.count == 0, (
            f"reading applied coupon codes cost {counter.count} queries "
            f"across {len(carts)} prefetched carts — the prefetch is "
            f"either missing or bypassed (values_list ignores it)"
        )
