"""The "on offer" resolver and everything that reads it.

``promotion.offers.offer_kind_expression`` is one SQL definition used by
the search index (``on_offer``), the product list/detail API
(``offerKind``) and the search cards. The rules are tested through the
queryset annotation; the consumers are checked once each.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from product.models.product import Product, ProductTranslation
from promotion.enum import (
    BenefitType,
    OfferKind,
    PromotionTrigger,
    TargetScope,
)
from promotion.factories.promotion import PromotionFactory
from promotion.models import PromotionRedemption
from search.serializers import ProductTranslationSerializer


@pytest.fixture(autouse=True)
def promotions_on():
    """Plan and merchant toggle both open (see test_public_offers)."""
    original = Setting.get.__func__

    def _get(cls, key, default=None):
        if key == "PROMOTIONS_ENABLED":
            return True
        return original(cls, key, default)

    with patch.object(Setting, "get", classmethod(_get)):
        yield


@pytest.fixture
def category(db):
    return ProductCategoryFactory()


@pytest.fixture
def product(db, category):
    return ProductFactory(
        category=category, discount_percent=Decimal(0), active=True
    )


def _promotion(**kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("target_scope", TargetScope.PRODUCTS)
    kwargs.setdefault("is_active", True)
    return PromotionFactory(**kwargs)


def _on_product(product, **kwargs):
    promotion = _promotion(**kwargs)
    promotion.products.set([product])
    return promotion


def _kind(product) -> str | None:
    return (
        Product.objects.with_offer_kind()
        .values_list("offer_kind_annotation", flat=True)
        .get(pk=product.pk)
    )


@pytest.mark.django_db
class TestMarkdown:
    def test_markdown_is_markdown(self, product):
        product.discount_percent = Decimal(15)
        product.save()

        assert _kind(product) == OfferKind.MARKDOWN

    def test_no_markdown_no_promotion_is_not_on_offer(self, product):
        assert _kind(product) is None

    def test_markdown_wins_over_a_promotion(self, product):
        product.discount_percent = Decimal(15)
        product.save()
        _on_product(product)

        assert _kind(product) == OfferKind.MARKDOWN


@pytest.mark.django_db
class TestPromotionRules:
    def test_automatic_product_scope_is_promotion(self, product):
        _on_product(product)

        assert _kind(product) == OfferKind.PROMOTION

    def test_code_promotion_is_not_an_offer(self, product):
        _on_product(product, trigger=PromotionTrigger.CODE)

        assert _kind(product) is None

    def test_promotion_naming_another_product_does_not_count(self, product):
        _on_product(ProductFactory(active=True))

        assert _kind(product) is None

    def test_order_scope_is_not_an_offer(self, product):
        _promotion(target_scope=TargetScope.ORDER)

        assert _kind(product) is None

    def test_category_scope_matches_the_category(self, product, category):
        _promotion(target_scope=TargetScope.CATEGORIES).categories.set(
            [category]
        )

        assert _kind(product) == OfferKind.PROMOTION

    def test_category_scope_reaches_descendant_categories(self, category):
        child = ProductCategoryFactory(parent=category)
        grandchild = ProductCategoryFactory(parent=child)
        deep = ProductFactory(
            category=grandchild, discount_percent=Decimal(0), active=True
        )
        _promotion(target_scope=TargetScope.CATEGORIES).categories.set(
            [category]
        )

        assert _kind(deep) == OfferKind.PROMOTION

    def test_category_scope_skips_a_parent_and_a_sibling_tree(self, category):
        child = ProductCategoryFactory(parent=category)
        parent_product = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )
        other_tree = ProductFactory(
            category=ProductCategoryFactory(),
            discount_percent=Decimal(0),
            active=True,
        )
        _promotion(target_scope=TargetScope.CATEGORIES).categories.set([child])

        assert _kind(parent_product) is None
        assert _kind(other_tree) is None

    @pytest.mark.parametrize(
        "benefit",
        [
            BenefitType.PERCENTAGE,
            BenefitType.FIXED_AMOUNT,
            BenefitType.BXGY,
            BenefitType.FREE_GIFT,
        ],
    )
    def test_price_benefits_count(self, product, benefit):
        _on_product(
            product, benefit_type=benefit, buy_quantity=1, get_quantity=1
        )

        assert _kind(product) == OfferKind.PROMOTION

    def test_free_shipping_is_not_an_offer(self, product):
        _on_product(product, benefit_type=BenefitType.FREE_SHIPPING)

        assert _kind(product) is None

    def test_promotions_switched_off_leave_only_markdowns(self, product):
        _on_product(product)
        marked_down = ProductFactory(
            category=product.category, discount_percent=Decimal(5), active=True
        )

        with patch(
            "promotion.services.PromotionEngine.is_enabled",
            return_value=False,
        ):
            assert _kind(product) is None
            assert _kind(marked_down) == OfferKind.MARKDOWN


@pytest.mark.django_db
class TestWindowAndActivity:
    def test_inactive_promotion_is_not_an_offer(self, product):
        _on_product(product, is_active=False)

        assert _kind(product) is None

    def test_not_started_yet(self, product):
        _on_product(product, starts_at=timezone.now() + timedelta(hours=1))

        assert _kind(product) is None

    def test_already_ended(self, product):
        _on_product(product, ends_at=timezone.now() - timedelta(hours=1))

        assert _kind(product) is None

    def test_inside_the_window(self, product):
        _on_product(
            product,
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=1),
        )

        assert _kind(product) == OfferKind.PROMOTION

    def test_open_ended_window_counts(self, product):
        _on_product(product, starts_at=None, ends_at=None)

        assert _kind(product) == OfferKind.PROMOTION


@pytest.mark.django_db
class TestExclusions:
    def test_excluded_product_is_not_covered(self, product, category):
        promotion = _promotion(target_scope=TargetScope.CATEGORIES)
        promotion.categories.set([category])
        promotion.excluded_products.set([product])
        sibling = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )

        assert _kind(product) is None
        assert _kind(sibling) == OfferKind.PROMOTION

    def test_excluded_category_removes_its_whole_subtree(self, category):
        child = ProductCategoryFactory(parent=category)
        in_child = ProductFactory(
            category=child, discount_percent=Decimal(0), active=True
        )
        in_root = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )
        promotion = _promotion(target_scope=TargetScope.CATEGORIES)
        promotion.categories.set([category])
        promotion.excluded_categories.set([child])

        assert _kind(in_child) is None
        assert _kind(in_root) == OfferKind.PROMOTION

    def test_exclude_discounted_still_leaves_the_markdown_on_offer(
        self, category
    ):
        marked_down = ProductFactory(
            category=category, discount_percent=Decimal(20), active=True
        )
        plain = ProductFactory(
            category=category, discount_percent=Decimal(0), active=True
        )
        promotion = _promotion(
            target_scope=TargetScope.CATEGORIES,
            exclude_discounted_products=True,
        )
        promotion.categories.set([category])

        assert _kind(marked_down) == OfferKind.MARKDOWN
        assert _kind(plain) == OfferKind.PROMOTION


@pytest.mark.django_db
class TestUsageLimit:
    @staticmethod
    def _redeem(promotion, times):
        for _ in range(times):
            PromotionRedemption.objects.create(
                promotion=promotion, amount=Money(Decimal(1), "EUR")
            )

    def test_reached_limit_is_not_an_offer(self, product):
        promotion = _on_product(product, usage_limit_total=2)
        self._redeem(promotion, 2)

        assert _kind(product) is None

    def test_limit_not_reached_is_an_offer(self, product):
        promotion = _on_product(product, usage_limit_total=2)
        self._redeem(promotion, 1)

        assert _kind(product) == OfferKind.PROMOTION

    def test_reached_limit_leaves_a_markdown_alone(self, product):
        product.discount_percent = Decimal(10)
        product.save()
        promotion = _on_product(product, usage_limit_total=1)
        self._redeem(promotion, 1)

        assert _kind(product) == OfferKind.MARKDOWN


@pytest.mark.django_db
class TestProductProperty:
    def test_bare_instance_resolves_itself(self, product):
        _on_product(product)

        assert Product.objects.get(pk=product.pk).offer_kind == (
            OfferKind.PROMOTION
        )

    def test_annotated_instance_costs_no_query(self, product):
        _on_product(product)
        annotated = Product.objects.with_offer_kind().get(pk=product.pk)

        with CaptureQueriesContext(connection) as queries:
            assert annotated.offer_kind == OfferKind.PROMOTION

        assert len(queries) == 0


@pytest.mark.django_db
class TestSearchIndexDocument:
    """``on_offer`` is what the ``onOffer`` search filter matches."""

    @staticmethod
    def _on_offer(product) -> bool:
        translation = ProductTranslation.get_meilisearch_queryset().get(
            master=product, language_code="en"
        )
        return translation.meili_serialize()["on_offer"]

    def test_markdown_product_is_indexed_on_offer(self, product):
        product.discount_percent = Decimal(10)
        product.save()

        assert self._on_offer(product) is True

    def test_promotion_product_is_indexed_on_offer(self, product):
        _on_product(product)

        assert self._on_offer(product) is True

    def test_plain_product_is_indexed_off_offer(self, product):
        assert self._on_offer(product) is False

    def test_single_document_path_resolves_without_the_annotation(
        self, product
    ):
        """``index_document_task`` loads a bare row, not the bulk
        queryset."""
        _on_product(product)
        translation = ProductTranslation.objects.get(
            master=product, language_code="en"
        )

        assert translation.meili_serialize()["on_offer"] is True

    def test_bulk_queryset_resolves_the_flag_inside_its_one_select(
        self, category
    ):
        """The rest of a document costs queries per row (attributes);
        the offer flag must not add any of its own."""

        def promotion_queries(count):
            products = ProductFactory.create_batch(
                count,
                category=category,
                discount_percent=Decimal(0),
                active=True,
                num_reviews=0,
            )
            _promotion(target_scope=TargetScope.CATEGORIES).categories.set(
                [category]
            )
            queryset = ProductTranslation.get_meilisearch_queryset().filter(
                master__in=products, language_code="en"
            )
            with CaptureQueriesContext(connection) as queries:
                for translation in queryset:
                    assert translation.meili_serialize()["on_offer"] is True
            return sum("promotion" in q["sql"].lower() for q in queries)

        assert promotion_queries(2) == 1
        assert promotion_queries(6) == 1


@pytest.mark.django_db
class TestApi:
    def _client(self):
        return APIClient()

    def test_list_and_detail_carry_offer_kind(self, product):
        _on_product(product)
        marked_down = ProductFactory(
            category=product.category, discount_percent=Decimal(10), active=True
        )
        plain = ProductFactory(
            category=product.category, discount_percent=Decimal(0), active=True
        )
        # The factory-built product may already carry a markdown; the
        # promotion product is the one under test.
        listing = self._client().get(reverse("product-list"))
        assert listing.status_code == status.HTTP_200_OK
        kinds = {
            row["id"]: row["offerKind"] for row in listing.json()["results"]
        }

        assert kinds[product.id] == OfferKind.PROMOTION
        assert kinds[marked_down.id] == OfferKind.MARKDOWN
        assert kinds[plain.id] is None

        detail = self._client().get(
            reverse("product-detail", kwargs={"pk": product.id})
        )
        assert detail.status_code == status.HTTP_200_OK
        assert detail.json()["offerKind"] == OfferKind.PROMOTION

    def test_list_query_count_does_not_grow_with_the_page(self, category):
        def queries_for(count):
            ProductFactory.create_batch(
                count,
                category=category,
                discount_percent=Decimal(0),
                active=True,
                num_reviews=0,
            )
            _promotion(target_scope=TargetScope.CATEGORIES).categories.set(
                [category]
            )
            with CaptureQueriesContext(connection) as queries:
                response = self._client().get(reverse("product-list"))
            assert response.status_code == status.HTTP_200_OK
            return len(queries)

        assert queries_for(8) <= queries_for(2) + 1

    def test_search_card_carries_offer_kind(self, product):
        _on_product(product)
        hit = (
            ProductTranslation.get_search_result_queryset()
            .filter(master=product, language_code="en")
            .get()
        )
        _ = hit.master  # prefetched with the hit

        with CaptureQueriesContext(connection) as queries:
            offer_kind = ProductTranslationSerializer(hit, context={}).data[
                "offer_kind"
            ]

        assert offer_kind == OfferKind.PROMOTION
        # The master was annotated by the queryset, so reading the badge
        # added no query beyond what the card already needs.
        assert not any("promotion" in q["sql"].lower() for q in queries)
