"""The live offers a single product's page advertises."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Prefetch

from promotion.engine.benefits import gift_entitlement
from promotion.engine.evaluation import PromotionEngine
from promotion.engine.scope import attach_category_scopes
from promotion.enum import BenefitType, ProductPromotionRelation, TargetScope
from promotion.managers.promotion import publishable_code_q
from promotion.models import Promotion, PromotionCode


@dataclass
class ProductOffer:
    """One live offer a product's page should advertise, with the
    reason it is relevant to THAT product."""

    promotion: Promotion
    relation: ProductPromotionRelation


class ProductPromotionService:
    """Which live offers apply to a single product.

    The ``/offers`` page answers "what is running"; this answers "what
    is running FOR THIS ITEM", which is the question a shopper actually
    has while looking at a product. Scope and exclusions are resolved
    with the same rules ``matching_items`` uses at
    cart time, so the page never advertises an offer the cart would
    silently skip.

    Read-only and cart-free: it cannot know whether the eventual cart
    will clear a minimum subtotal, so the conditions travel with the
    row and the storefront renders them as fine print.
    """

    # Specific beats general, for both the relation chosen per
    # promotion and the order the page lists them in. REWARD outranks
    # CATEGORY because "you can get this one free" is a stronger claim
    # about the item in front of the shopper than "its category is on
    # offer"; it loses to PRODUCT, which is the promotion naming the
    # item outright.
    _RELATION_RANK = {
        ProductPromotionRelation.PRODUCT: 0,
        ProductPromotionRelation.REWARD: 1,
        ProductPromotionRelation.CATEGORY: 2,
        ProductPromotionRelation.ORDER: 3,
    }

    @classmethod
    def for_product(cls, product) -> list[ProductOffer]:
        """Publicly advertisable live offers touching ``product``."""
        if not PromotionEngine.is_enabled():
            return []

        promotions = list(
            Promotion.objects.live()
            .publicly_listable()
            .for_list()
            .with_scope_relations()
            .prefetch_related(
                Prefetch(
                    "codes",
                    queryset=PromotionCode.objects.filter(
                        publishable_code_q()
                    ).order_by("created_at"),
                    to_attr="publishable_codes",
                ),
                "products__translations",
                "get_products__translations",
                "categories__translations",
            )
            .order_by("priority", "id")
        )
        attach_category_scopes(promotions)

        offers = [
            ProductOffer(promotion, relation)
            for promotion in promotions
            if (relation := cls._relation(promotion, product)) is not None
        ]
        offers.sort(
            key=lambda offer: (
                cls._RELATION_RANK[offer.relation],
                offer.promotion.priority,
                offer.promotion.id,
            )
        )
        return offers

    @classmethod
    def _relation(
        cls, promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        """Why this promotion matters on this product's page, or None.

        The buy side is checked first and only counts when the product
        survives the promotion's exclusions — an excluded product is
        exactly the case where showing the offer would be a lie. The
        reward side is a separate claim ("this item is the gift") and
        carries no such exclusions, because the exclusion lists
        constrain what the shopper must BUY, not what they receive.
        """
        buy = cls._buy_relation(promotion, product)
        reward = cls._reward_relation(promotion, product)
        if buy is None:
            return reward
        if reward is None:
            return buy
        return min(buy, reward, key=lambda rel: cls._RELATION_RANK[rel])

    @staticmethod
    def _buy_relation(
        promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        excluded_product_ids = {p.id for p in promotion.excluded_products.all()}
        if product.id in excluded_product_ids:
            return None
        if product.category_id in promotion._excluded_category_ids:
            return None
        if (
            promotion.exclude_discounted_products
            and (product.discount_percent or 0) > 0
        ):
            return None

        if promotion.target_scope == TargetScope.PRODUCTS:
            included = {p.id for p in promotion.products.all()}
            return (
                ProductPromotionRelation.PRODUCT
                if product.id in included
                else None
            )
        if promotion.target_scope == TargetScope.CATEGORIES:
            return (
                ProductPromotionRelation.CATEGORY
                if product.category_id in promotion._included_category_ids
                else None
            )
        return ProductPromotionRelation.ORDER

    @staticmethod
    def _reward_relation(
        promotion: Promotion, product
    ) -> ProductPromotionRelation | None:
        """Whether the shopper RECEIVES this product from the offer.

        FREE_GIFT mirrors ``gift_entitlement`` exactly
        — lowest-pk active gift product with the stock to hand over —
        rather than "is in the get_products list", so a promotion with
        two gifts never promises the one the engine would not hand out.
        """
        if promotion.benefit_type == BenefitType.FREE_GIFT:
            entitlement = gift_entitlement(promotion)
            return (
                ProductPromotionRelation.REWARD
                if entitlement is not None
                and entitlement.product.id == product.id
                else None
            )
        if promotion.benefit_type != BenefitType.BXGY:
            return None
        reward_ids = {p.id for p in promotion.get_products.all()}
        return (
            ProductPromotionRelation.REWARD
            if product.id in reward_ids
            else None
        )
