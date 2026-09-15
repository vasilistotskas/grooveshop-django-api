"""The per-product offers endpoint.

Two things carry the weight here. First the RELATION: the storefront
phrases the offer from it, and a promotion can reach a product four
different ways (it names it, it gives it away, it targets its category,
it applies to every order). Second the EXCLUSIONS — the whole point of
resolving scope server-side is that an excluded product must not
advertise the offer the cart engine would skip for it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from promotion.enum import (
    BenefitType,
    ProductPromotionRelation,
    PromotionTrigger,
    TargetScope,
)
from promotion.factories.promotion import (
    PromotionCodeFactory,
    PromotionFactory,
    PromotionTranslationFactory,
)


@pytest.fixture
def promotions_on():
    """Both gate tiers open — see test_public_offers for the reasoning."""
    original = Setting.get.__func__

    def _get(cls, key, default=None):
        if key == "PROMOTIONS_ENABLED":
            return True
        return original(cls, key, default)

    with patch.object(Setting, "get", classmethod(_get)):
        yield


@pytest.fixture
def client():
    return APIClient()


def url_for(product) -> str:
    return reverse(
        "promotion:promotion-product-list",
        kwargs={"product_id": product.id},
    )


@pytest.fixture
def category(db):
    return ProductCategoryFactory()


@pytest.fixture
def product(db, category):
    return ProductFactory(
        category=category,
        discount_percent=Decimal(0),
        active=True,
    )


def _named(promotion, name="Offer"):
    PromotionTranslationFactory(master=promotion, language_code="el", name=name)
    return promotion


def _promotion(**kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("is_active", True)
    return _named(PromotionFactory(**kwargs))


def _relations(response) -> dict[int, str]:
    return {row["id"]: row["relation"] for row in response.json()}


@pytest.mark.django_db
class TestRelation:
    def test_order_scope_applies_to_every_product(
        self, client, product, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.ORDER)

        response = client.get(url_for(product))

        assert response.status_code == status.HTTP_200_OK
        assert _relations(response) == {
            promotion.id: ProductPromotionRelation.ORDER.value
        }

    def test_product_scope_names_the_product(
        self, client, product, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.PRODUCTS)
        promotion.products.set([product])

        assert _relations(client.get(url_for(product))) == {
            promotion.id: ProductPromotionRelation.PRODUCT.value
        }

    def test_product_scope_skips_a_product_it_does_not_name(
        self, client, product, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.PRODUCTS)
        promotion.products.set([ProductFactory()])

        assert client.get(url_for(product)).json() == []

    def test_category_scope_matches_through_the_category(
        self, client, product, category, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.CATEGORIES)
        promotion.categories.set([category])

        assert _relations(client.get(url_for(product))) == {
            promotion.id: ProductPromotionRelation.CATEGORY.value
        }

    def test_category_scope_matches_through_a_parent_category(
        self, client, category, promotions_on
    ):
        """MPTT descendants count, exactly as the cart engine counts
        them — a promotion on a parent category reaches the children."""
        child = ProductCategoryFactory(parent=category)
        child_product = ProductFactory(
            category=child, discount_percent=Decimal(0), active=True
        )
        promotion = _promotion(target_scope=TargetScope.CATEGORIES)
        promotion.categories.set([category])

        assert _relations(client.get(url_for(child_product))) == {
            promotion.id: ProductPromotionRelation.CATEGORY.value
        }

    def test_free_gift_reports_the_product_as_the_reward(
        self, client, product, promotions_on
    ):
        """ORDER scope would technically match too; REWARD is the more
        informative claim and must win."""
        promotion = _promotion(
            benefit_type=BenefitType.FREE_GIFT,
            target_scope=TargetScope.ORDER,
            get_quantity=1,
        )
        promotion.get_products.set([product])

        assert _relations(client.get(url_for(product))) == {
            promotion.id: ProductPromotionRelation.REWARD.value
        }

    def test_free_gift_claims_only_the_product_the_engine_would_hand_out(
        self, client, category, promotions_on
    ):
        """A FREE_GIFT is documented as carrying exactly one gift
        product, and ``_gift_entitlement`` resolves a mis-configured
        pair to the lowest-pk ACTIVE one. The runner-up must not promise
        a gift nobody would ever receive — its ORDER scope is still
        real, so the offer shows, just not as a reward."""
        chosen = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )
        runner_up = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )
        promotion = _promotion(
            benefit_type=BenefitType.FREE_GIFT,
            target_scope=TargetScope.ORDER,
            get_quantity=1,
        )
        promotion.get_products.set([chosen, runner_up])

        assert _relations(client.get(url_for(chosen))) == {
            promotion.id: ProductPromotionRelation.REWARD.value
        }
        assert _relations(client.get(url_for(runner_up))) == {
            promotion.id: ProductPromotionRelation.ORDER.value
        }

    def test_bxgy_reward_pool_reports_the_reward(
        self, client, product, promotions_on
    ):
        promotion = _promotion(
            benefit_type=BenefitType.BXGY,
            target_scope=TargetScope.ORDER,
            buy_quantity=2,
            get_quantity=1,
        )
        promotion.get_products.set([product])

        assert _relations(client.get(url_for(product))) == {
            promotion.id: ProductPromotionRelation.REWARD.value
        }


@pytest.mark.django_db
class TestExclusions:
    def test_an_excluded_product_does_not_advertise_the_offer(
        self, client, product, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.ORDER)
        promotion.excluded_products.set([product])

        assert client.get(url_for(product)).json() == []

    def test_an_excluded_category_does_not_advertise_the_offer(
        self, client, product, category, promotions_on
    ):
        promotion = _promotion(target_scope=TargetScope.ORDER)
        promotion.excluded_categories.set([category])

        assert client.get(url_for(product)).json() == []

    def test_excluded_descendants_are_excluded_too(
        self, client, category, promotions_on
    ):
        child = ProductCategoryFactory(parent=category)
        child_product = ProductFactory(
            category=child, discount_percent=Decimal(0), active=True
        )
        promotion = _promotion(target_scope=TargetScope.ORDER)
        promotion.excluded_categories.set([category])

        assert client.get(url_for(child_product)).json() == []

    def test_already_discounted_product_skips_a_no_sale_items_offer(
        self, client, category, promotions_on
    ):
        marked_down = ProductFactory(
            category=category, discount_percent=Decimal(20), active=True
        )
        _promotion(
            target_scope=TargetScope.ORDER,
            exclude_discounted_products=True,
        )

        assert client.get(url_for(marked_down)).json() == []

    def test_an_excluded_product_can_still_be_the_reward(
        self, client, product, promotions_on
    ):
        """The exclusion lists constrain what the shopper must BUY. A
        gift the shopper receives is not a purchase, so excluding the
        product from the buy side must not hide the gift claim."""
        promotion = _promotion(
            benefit_type=BenefitType.FREE_GIFT,
            target_scope=TargetScope.ORDER,
            get_quantity=1,
        )
        promotion.get_products.set([product])
        promotion.excluded_products.set([product])

        assert _relations(client.get(url_for(product))) == {
            promotion.id: ProductPromotionRelation.REWARD.value
        }


@pytest.mark.django_db
class TestVisibilityMirrorsTheOffersPage:
    def test_excludes_a_promotion_that_is_not_live(
        self, client, product, promotions_on
    ):
        _promotion(
            target_scope=TargetScope.ORDER,
            starts_at=timezone.now() + timedelta(days=1),
        )

        assert client.get(url_for(product)).json() == []

    def test_excludes_a_code_promotion_with_no_advertisable_code(
        self, client, product, promotions_on
    ):
        """A personal coupon is not advertisable anywhere, and the
        product page is as crawlable as the offers page."""
        promotion = _promotion(
            trigger=PromotionTrigger.CODE, target_scope=TargetScope.ORDER
        )
        PromotionCodeFactory(
            promotion=promotion,
            code="PRIVATE1",
            assigned_to_email="someone@example.com",
        )

        assert client.get(url_for(product)).json() == []

    def test_publishes_the_code_of_an_advertisable_code_promotion(
        self, client, product, promotions_on
    ):
        promotion = _promotion(
            trigger=PromotionTrigger.CODE, target_scope=TargetScope.ORDER
        )
        PromotionCodeFactory(promotion=promotion, code="PUBLIC10")

        rows = client.get(url_for(product)).json()

        assert [row["code"] for row in rows] == ["PUBLIC10"]


@pytest.mark.django_db
class TestOrderingAndGates:
    def test_specific_offers_come_before_store_wide_ones(
        self, client, product, category, promotions_on
    ):
        store_wide = _promotion(target_scope=TargetScope.ORDER, priority=0)
        by_category = _promotion(
            target_scope=TargetScope.CATEGORIES, priority=0
        )
        by_category.categories.set([category])
        by_product = _promotion(target_scope=TargetScope.PRODUCTS, priority=0)
        by_product.products.set([product])

        rows = client.get(url_for(product)).json()

        assert [row["id"] for row in rows] == [
            by_product.id,
            by_category.id,
            store_wide.id,
        ]

    def test_unknown_product_is_a_404(self, client, promotions_on):
        response = client.get(
            reverse(
                "promotion:promotion-product-list",
                kwargs={"product_id": 999_999},
            )
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_inactive_product_is_a_404(self, client, category, promotions_on):
        hidden = ProductFactory(category=category, active=False)

        response = client.get(url_for(hidden))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_runtime_gate_off_is_a_404(self, client, product):
        """No ``promotions_on`` fixture: the merchant toggle is off, and
        the route must be indistinguishable from one that never existed.
        """
        _promotion(target_scope=TargetScope.ORDER)

        response = client.get(url_for(product))

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestPayload:
    def test_carries_the_same_fields_as_the_offers_page(
        self, client, product, promotions_on
    ):
        """One offer shape across /offers, the product panel and the
        checkout picker is what lets the storefront share a single card
        component and headline formatter."""
        promotion = _promotion(
            target_scope=TargetScope.ORDER,
            benefit_type=BenefitType.PERCENTAGE,
            benefit_value=Decimal("15.00"),
            min_subtotal=Money(Decimal("40.00"), "EUR"),
            max_discount_amount=Money(Decimal("10.00"), "EUR"),
            first_order_only=True,
        )

        row = client.get(url_for(product)).json()[0]

        assert row["id"] == promotion.id
        assert row["relation"] == ProductPromotionRelation.ORDER.value
        assert row["benefitType"] == BenefitType.PERCENTAGE.value
        assert Decimal(str(row["benefitValue"])) == Decimal("15.00")
        assert Decimal(str(row["minSubtotal"])) == Decimal("40.00")
        assert Decimal(str(row["maxDiscountAmount"])) == Decimal("10.00")
        assert row["firstOrderOnly"] is True
        assert row["rewardProducts"] == []
        assert row["eligibleCategories"] == []
