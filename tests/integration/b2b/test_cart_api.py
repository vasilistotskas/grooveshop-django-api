"""API-level binding regressions — the gap the first review round
exposed: the service bound one cart instance while the views serialized
freshly reloaded ones, so GET /cart showed retail and the Stripe intent
was minted on the retail total (guaranteed
``PaymentAmountMismatchError`` for every wholesale card checkout).
These tests pin the WIRE behavior, not the service layer.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from b2b.factories import BusinessProfileFactory, CustomerGroupFactory
from cart.factories import CartFactory, CartItemFactory
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db


def _wholesale_buyer(discount="10.00", **group_kwargs):
    group = CustomerGroupFactory(
        discount_percent=Decimal(discount), **group_kwargs
    )
    profile = BusinessProfileFactory(approved=True, customer_group=group)
    return profile.user, group


def _cart_with_line(user, price="100.00", quantity=1):
    cart = CartFactory(user=user)
    cart.items.all().delete()
    product = ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        stock=10,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=quantity)
    return cart, product


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestCartReadBinding:
    def test_get_cart_returns_wholesale_prices_and_badge(
        self, b2b_tenant, enable_wholesale
    ):
        user, group = _wholesale_buyer(discount="10.00")
        _cart_with_line(user, "100.00", quantity=2)

        response = _client(user).get(reverse("cart-detail"))

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        # Pre-render response.data: Decimals, snake_case.
        assert data["total_price"] == Decimal("180.00")  # 2 × 90 net
        assert data["b2b_pricing"]["applied"] is True
        assert data["b2b_pricing"]["group_name"] == group.name
        assert data["b2b_pricing"]["allow_promotions"] is False
        assert data["b2b_pricing"]["below_minimum"] is False
        line = data["items"][0]
        assert line["final_price"] == Decimal("90.00")

    def test_get_cart_stays_retail_for_pending_profile(
        self, b2b_tenant, enable_wholesale
    ):
        profile = BusinessProfileFactory(
            customer_group=CustomerGroupFactory(discount_percent=Decimal(10))
        )  # PENDING
        _cart_with_line(profile.user, "100.00")

        response = _client(profile.user).get(reverse("cart-detail"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_price"] == Decimal("100.00")
        assert response.data["b2b_pricing"] is None

    def test_below_minimum_flag(self, b2b_tenant, enable_wholesale):
        user, _group = _wholesale_buyer(
            discount="10.00", min_order_value=Money("500.00", "EUR")
        )
        _cart_with_line(user, "100.00")

        response = _client(user).get(reverse("cart-detail"))

        assert response.data["b2b_pricing"]["below_minimum"] is True
        assert response.data["b2b_pricing"]["min_order_value"] == "500.00"

    def test_get_cart_item_endpoint_returns_wholesale_line(
        self, b2b_tenant, enable_wholesale
    ):
        """The item endpoints materialize carts via select_related —
        they must price bound lines wholesale too."""
        user, _group = _wholesale_buyer(discount="10.00")
        cart, _product = _cart_with_line(user, "100.00")
        item = cart.items.first()

        response = _client(user).get(
            reverse("cart-item-detail", args=[item.pk])
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["final_price"] == Decimal("90.00")


class TestCouponGateOnWire:
    def test_apply_coupon_refused_on_wholesale_cart(
        self, b2b_tenant, enable_wholesale
    ):
        from promotion.factories import (
            PromotionCodeFactory,
            PromotionFactory,
        )

        user, _group = _wholesale_buyer()
        _cart_with_line(user, "100.00")
        # The coupon endpoint is plan-gated on promotions too.
        b2b_tenant.promotions_enabled = True
        b2b_tenant.save(update_fields=["promotions_enabled"])
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10))
        )

        def _get(key, default=None):
            return {
                "B2B_WHOLESALE_ENABLED": True,
                "PROMOTIONS_ENABLED": True,
            }.get(key, default)

        with (
            patch("b2b.services.Setting.get", side_effect=_get),
            patch("promotion.services.Setting.get", side_effect=_get),
        ):
            response = _client(user).post(
                reverse("cart-coupon"), {"code": code.code}, format="json"
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["reason"] == "discount_code_combination_disallowed"
        # The dead code must not stay attached.
        from promotion.models import CartPromotionCode

        assert not CartPromotionCode.objects.filter(cart__user=user).exists()


class TestPaymentIntentBinding:
    def _intent_payload(self, pay_way):
        return {
            "pay_way_id": pay_way.id,
            "shipping_kind": "home_delivery",
        }

    def test_intent_amount_uses_wholesale_total(
        self, b2b_tenant, enable_wholesale
    ):
        from pay_way.factories import PayWayFactory

        user, _group = _wholesale_buyer(discount="10.00")
        _cart_with_line(user, "100.00")
        pay_way = PayWayFactory(
            is_online_payment=True,
            provider_code="stripe",
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        )

        provider = MagicMock()
        provider.process_payment.return_value = (
            True,
            {"payment_id": "pi_test", "client_secret": "cs_test"},
        )

        def _get(key, default=None):
            # Free shipping above 50€ makes the expected charge exact:
            # the 90.00 wholesale items total clears it (the 100.00
            # retail total would too — the assertion below tells the
            # two apart by the amount itself).
            return {
                "B2B_WHOLESALE_ENABLED": True,
                "FREE_SHIPPING_THRESHOLD": Decimal("50.00"),
            }.get(key, default)

        with (
            patch(
                "pay_way.services.PayWayService.get_provider_for_pay_way",
                return_value=provider,
            ),
            patch("extra_settings.models.Setting.get", side_effect=_get),
            patch("b2b.services.Setting.get", side_effect=_get),
        ):
            response = _client(user).post(
                reverse("cart-create-payment-intent"),
                self._intent_payload(pay_way),
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.data
        charged = provider.process_payment.call_args.kwargs["amount"]
        # Wholesale items total, free shipping, no fee — NEVER the
        # 100.00 retail base the pre-fix views were minting on.
        assert charged.amount == Decimal("90.00")

    def test_intent_refused_below_wholesale_minimum(
        self, b2b_tenant, enable_wholesale
    ):
        from pay_way.factories import PayWayFactory

        user, _group = _wholesale_buyer(
            discount="10.00", min_order_value=Money("500.00", "EUR")
        )
        _cart_with_line(user, "100.00")
        pay_way = PayWayFactory(
            is_online_payment=True,
            provider_code="stripe",
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        )

        response = _client(user).post(
            reverse("cart-create-payment-intent"),
            self._intent_payload(pay_way),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Match the amount, not the wording — the message is localized
        # (Greek when the compiled .mo is present, English in CI).
        assert "500" in str(response.data["detail"])
