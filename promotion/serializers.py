"""Public read-only serializers for the storefront's offers page.

WRITE-SIDE NOTE: promotions are authored in the Django admin, so there
is no write serializer here on purpose. This module exists only to
publish what a shopper is allowed to know.

What must never appear in this payload:

* ``usage_limit_total`` / ``usage_limit_per_customer`` — publishing how
  many redemptions are left invites a race and tells competitors the
  budget of a campaign.
* ``PromotionCode.assigned_to`` / ``assigned_to_email`` — a personal
  coupon's owner. The queryset in ``promotion/views.py`` already
  excludes personal codes; the filter in ``get_code`` is the second
  line of defence, so a future queryset change cannot leak one.
* ``priority`` and the exclusion M2Ms — internal engine mechanics with
  no shopper meaning, and the exclusion lists would leak catalogue
  structure.

What deliberately IS published, because withholding it would make the
page misleading rather than safe: ``min_subtotal`` (the shopper cannot
act on the offer without it), ``max_discount_amount`` (hiding the cap
overstates the benefit), ``exclude_discounted_products`` and
``min_quantity`` (ordinary retail fine print), and ``ends_at``.

Every ``SerializerMethodField`` carries an ``@extend_schema_field``:
the Nuxt client generates its TypeScript types AND its Zod validators
from this schema, and an unannotated method field lands as an untyped
``any``, which means the storefront proxy silently stops validating it.
"""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from promotion.models import Promotion

# A reward or eligibility pool bigger than this is a catalogue-wide
# promotion; listing every item would bloat the payload for no gain, so
# the page shows the first few and links to the category instead.
REWARD_PREVIEW_LIMIT = 12


class PromotionProductRefSerializer(serializers.Serializer):
    """The minimum a storefront product card needs to render."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    slug = serializers.CharField(read_only=True)
    main_image_path = serializers.CharField(read_only=True)

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return (
            obj.safe_translation_getter("name", any_language=True) or obj.slug
        )


class PromotionCategoryRefSerializer(serializers.Serializer):
    """Enough to link a promotion at its category listing page."""

    id = serializers.IntegerField(read_only=True)
    slug = serializers.CharField(read_only=True)
    name = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return (
            obj.safe_translation_getter("name", any_language=True) or obj.slug
        )


def _publishable(code) -> bool:
    """Whether a shopper may be shown this code.

    A personal coupon (assigned to a user or an email) is not
    advertisable, and neither is a single-use code: ``usage_limit=1``
    means a bulk code handed out individually (see the field's help
    text), not a first-come-first-served offer. See
    ``promotion.views.publishable_code_q`` for the full reasoning.
    """
    return bool(
        code.is_active
        and code.assigned_to_id is None
        and not code.assigned_to_email
        and code.usage_limit != 1
    )


class PublicPromotionSerializer(serializers.ModelSerializer):
    """One live, publicly-advertisable promotion."""

    name = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    code = serializers.SerializerMethodField()
    min_subtotal = serializers.SerializerMethodField()
    max_discount_amount = serializers.SerializerMethodField()
    reward_products = serializers.SerializerMethodField()
    eligible_products = serializers.SerializerMethodField()
    eligible_product_count = serializers.SerializerMethodField()
    eligible_categories = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = (
            "id",
            "name",
            "description",
            "trigger",
            "benefit_type",
            "benefit_value",
            "target_scope",
            "code",
            "min_subtotal",
            "max_discount_amount",
            "min_quantity",
            "buy_quantity",
            "get_quantity",
            "get_discount_percent",
            "exclude_discounted_products",
            "first_order_only",
            "stackable",
            "ends_at",
            "reward_products",
            "eligible_products",
            "eligible_product_count",
            "eligible_categories",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj: Promotion) -> str:
        return obj.safe_translation_getter("name", any_language=True) or ""

    @extend_schema_field(serializers.CharField())
    def get_description(self, obj: Promotion) -> str:
        return (
            obj.safe_translation_getter("description", any_language=True) or ""
        )

    @extend_schema_field(
        serializers.CharField(
            allow_null=True,
            help_text=(
                "The coupon code to enter at checkout. Null for an "
                "AUTOMATIC promotion, which needs no code."
            ),
        )
    )
    def get_code(self, obj: Promotion) -> str | None:
        """The one code a shopper may be told about, or ``None``.

        ``publishable_codes`` is attached by the view's prefetch so this
        never queries per row.
        """
        codes = getattr(obj, "publishable_codes", None)
        if codes is None:
            codes = [code for code in obj.codes.all() if _publishable(code)]
        return codes[0].code if codes else None

    # NULLABILITY IS LOAD-BEARING on both money fields below. The
    # storefront proxy validates this payload with a Zod schema
    # generated from the OpenAPI document, so a field that can be null
    # but is not declared nullable makes the proxy REJECT every offer
    # without a threshold — which is most of them. ``OpenApiTypes.DECIMAL``
    # alone does not carry nullability, hence the explicit field.
    #
    # They return a raw ``Decimal`` rather than a string because the
    # project sets ``COERCE_DECIMAL_TO_STRING: False``, so every other
    # money field on the API (``Cart.promotion_discount`` and friends)
    # renders as a JSON number.
    @extend_schema_field(
        serializers.DecimalField(
            max_digits=11,
            decimal_places=2,
            allow_null=True,
            help_text="Cart items total (incl. VAT) the offer requires.",
        )
    )
    def get_min_subtotal(self, obj: Promotion) -> Decimal | None:
        return obj.min_subtotal.amount if obj.min_subtotal is not None else None

    @extend_schema_field(
        serializers.DecimalField(
            max_digits=11,
            decimal_places=2,
            allow_null=True,
            help_text="Ceiling on a single application's discount.",
        )
    )
    def get_max_discount_amount(self, obj: Promotion) -> Decimal | None:
        amount = getattr(obj, "max_discount_amount", None)
        return amount.amount if amount is not None else None

    @extend_schema_field(PromotionProductRefSerializer(many=True))
    def get_reward_products(self, obj: Promotion) -> list:
        """The gift (FREE_GIFT) or the discounted units (BXGY).

        Empty for the other benefit types, and empty for a BXGY whose
        reward pool is "the same products as the buy side" — the model's
        documented meaning for an empty ``get_products``.
        """
        products = list(obj.get_products.all())[:REWARD_PREVIEW_LIMIT]
        return list(PromotionProductRefSerializer(products, many=True).data)

    @extend_schema_field(PromotionProductRefSerializer(many=True))
    def get_eligible_products(self, obj: Promotion) -> list:
        products = list(obj.products.all())[:REWARD_PREVIEW_LIMIT]
        return list(PromotionProductRefSerializer(products, many=True).data)

    @extend_schema_field(serializers.IntegerField())
    def get_eligible_product_count(self, obj: Promotion) -> int:
        """Total, not the truncated preview — the page needs it to decide
        whether to render a "see all" link."""
        return obj.products.count()

    @extend_schema_field(PromotionCategoryRefSerializer(many=True))
    def get_eligible_categories(self, obj: Promotion) -> list:
        return list(
            PromotionCategoryRefSerializer(obj.categories.all(), many=True).data
        )
