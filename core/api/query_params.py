from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

# The most ids one comma-separated query parameter may carry. Every caller
# inherits it unless it bounds the list itself right after parsing (the
# loyalty points preview, with its own documented error). The public product
# search turns these lists into Meilisearch ``IN [...]`` filters (categories
# expanded to their whole subtree), so an unbounded list is an unbounded
# filter. The storefront builds them from the filter chips a shopper ticks,
# one id per chip, plus the listing's own category: a real selection is a
# handful of ids, tens at the very most. 100 is well above that and matches
# ``MAX_POINTS_PREVIEW_PRODUCTS``.
MAX_INT_CSV_ITEMS = 100


def parse_int_csv(
    value: str | None,
    name: str,
    *,
    max_items: int | None = MAX_INT_CSV_ITEMS,
) -> list[int]:
    """Parse a comma-separated list of integers, 400 (not 500) on garbage.

    Also 400 when the list holds more than ``max_items`` entries. The count
    is taken before any value is converted, so an oversized list costs one
    split and nothing more. ``max_items=None`` is only for a caller that
    enforces its own documented bound on the result right away, so that its
    error, not this one, answers an oversized list.
    """
    if not value:
        return []
    # Distinct values, first occurrence kept: a link that repeats an id (a
    # mangled or stale filter URL) must not cost the shopper the listing.
    parts = list(
        dict.fromkeys(v.strip() for v in value.split(",") if v.strip())
    )
    if max_items is not None and len(parts) > max_items:
        raise ValidationError(
            {name: _("Give at most %(max)d values.") % {"max": max_items}},
            code="too_many_values",
        )
    try:
        return [int(v) for v in parts]
    except ValueError, TypeError:
        raise ValidationError(
            {name: _("Must be a comma-separated list of integers.")},
            code="invalid_integer_list",
        )
