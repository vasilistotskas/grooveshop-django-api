"""/api/v1/b2b/prices — wholesale price lookups for the storefront's
client-side hydration. Never cached; returns [] rather than leaking
whether the caller is in the program."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from b2b.factories import (
    BusinessProfileFactory,
    CustomerGroupFactory,
    PriceListItemFactory,
)
from product.factories import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _product(price="100.00"):
    return ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal("0"),
        vat=None,
        stock=10,
        active=True,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class TestPricesEndpoint:
    def test_approved_buyer_gets_group_prices(
        self, b2b_tenant, enable_wholesale
    ):
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        percent_product = _product("100.00")
        override_product = _product("100.00")
        PriceListItemFactory(
            group=group,
            product=override_product,
            net_price=Money("70.00", "EUR"),
        )

        response = _client(profile.user).get(
            reverse("b2b:b2b-prices"),
            {"ids": f"{percent_product.pk},{override_product.pk}"},
        )

        assert response.status_code == status.HTTP_200_OK
        # snake_case Decimals — DRF test response.data is pre-render
        # (no camelCase transform, no string coercion).
        by_id = {row["product_id"]: row for row in response.data}
        assert by_id[percent_product.pk]["final_price"] == Decimal("90.00")
        assert by_id[override_product.pk]["final_price"] == Decimal("70.00")
        assert by_id[percent_product.pk]["discount_percent"] == Decimal("10.00")

    def test_unapproved_buyer_gets_empty_array(
        self, b2b_tenant, enable_wholesale
    ):
        profile = BusinessProfileFactory()  # PENDING, no group
        product = _product()

        response = _client(profile.user).get(
            reverse("b2b:b2b-prices"), {"ids": str(product.pk)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_no_profile_gets_empty_array(self, b2b_tenant, enable_wholesale):
        product = _product()

        response = _client(UserAccountFactory()).get(
            reverse("b2b:b2b-prices"), {"ids": str(product.pk)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_inactive_product_excluded(self, b2b_tenant, enable_wholesale):
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        inactive = ProductFactory(
            price=Money(Decimal("100.00"), "EUR"),
            discount_percent=Decimal("0"),
            vat=None,
            stock=10,
            active=False,
        )

        response = _client(profile.user).get(
            reverse("b2b:b2b-prices"), {"ids": str(inactive.pk)}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_malformed_ids_400(self, b2b_tenant, enable_wholesale):
        profile = BusinessProfileFactory(approved=True)

        response = _client(profile.user).get(
            reverse("b2b:b2b-prices"), {"ids": "1,abc"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_anonymous_gets_auth_error(self, b2b_tenant, enable_wholesale):
        response = APIClient().get(reverse("b2b:b2b-prices"), {"ids": "1"})

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
