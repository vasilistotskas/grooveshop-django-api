"""Wire shapes for the recommendation endpoints.

The response carries serialized products PLUS a ``reason`` per item —
the strategy that produced it, the curated relation type when there
is one, and the blended score — so the storefront can label a card
("Goes well with"), a merchant can audit it, and support can answer
"why did it show that". Never free text: the storefront maps the enum
to a translated string.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from product.enum.relation import RelationType
from product.serializers.product import ProductSerializer
from recommendation.enum import EventKind, StrategyCode, Surface

_MAX_LIMIT = 12
_MAX_SEEDS = 24


def _int_list(raw: str, *, field: str) -> list[int]:
    if not raw:
        return []
    try:
        values = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise serializers.ValidationError(
            {field: _("Must be a comma-separated list of product ids.")}
        ) from exc
    if any(value < 1 for value in values):
        raise serializers.ValidationError(
            {field: _("Product ids must be positive.")}
        )
    return values


class RecommendationQuerySerializer(serializers.Serializer):
    surface = serializers.ChoiceField(
        choices=Surface.choices,
        default=Surface.PDP,
        help_text=_("Where the strip is rendered; selects the slot."),
    )
    seed = serializers.IntegerField(
        min_value=1,
        required=False,
        help_text=_("The product the shopper is looking at."),
    )
    seeds = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_(
            "Comma-separated product ids for multi-seed surfaces "
            "(cart lines, recently viewed)."
        ),
    )
    exclude = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Comma-separated product ids never to suggest."),
    )
    limit = serializers.IntegerField(
        min_value=1,
        max_value=_MAX_LIMIT,
        required=False,
        help_text=_("Override the slot's limit, capped at 12."),
    )

    def validate(self, attrs):
        seed_ids: list[int] = []
        if attrs.get("seed"):
            seed_ids.append(attrs["seed"])
        seed_ids.extend(_int_list(attrs.get("seeds", ""), field="seeds"))
        seed_ids = list(dict.fromkeys(seed_ids))[:_MAX_SEEDS]
        if not seed_ids and attrs["surface"] != Surface.EMPTY_CART:
            raise serializers.ValidationError(
                {"seed": _("A seed product is required for this surface.")}
            )
        attrs["seed_ids"] = seed_ids
        attrs["exclude_ids"] = _int_list(
            attrs.get("exclude", ""), field="exclude"
        )
        return attrs


class RecommendationReasonSerializer(serializers.Serializer):
    strategy = serializers.ChoiceField(choices=StrategyCode.choices)
    relation_type = serializers.ChoiceField(
        choices=RelationType.choices, allow_null=True
    )
    score = serializers.FloatField()


class RecommendationItemSerializer(serializers.Serializer):
    product = ProductSerializer(read_only=True)
    reason = RecommendationReasonSerializer(read_only=True)


class RecommendationResponseSerializer(serializers.Serializer):
    surface = serializers.ChoiceField(choices=Surface.choices)
    items = RecommendationItemSerializer(many=True, read_only=True)
    impression_id = serializers.UUIDField(
        help_text=_("Echo on click events so attach can be attributed.")
    )


class RecommendationEventItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    strategy = serializers.ChoiceField(choices=StrategyCode.choices)
    position = serializers.IntegerField(min_value=0, required=False)


class RecommendationEventRequestSerializer(serializers.Serializer):
    impression_id = serializers.UUIDField()
    surface = serializers.ChoiceField(choices=Surface.choices)
    # ``attach`` is written server-side by order completion only.
    kind = serializers.ChoiceField(
        choices=[
            (EventKind.IMPRESSION, EventKind.IMPRESSION.label),
            (EventKind.CLICK, EventKind.CLICK.label),
        ]
    )
    seed_id = serializers.IntegerField(min_value=1, required=False)
    items = RecommendationEventItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError(_("At least one item."))
        if len(value) > _MAX_SEEDS:
            raise serializers.ValidationError(
                _("At most %(n)s items per event.") % {"n": _MAX_SEEDS}
            )
        return value


class RecommendationEventResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
