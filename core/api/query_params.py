from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

# The most ids one comma-separated query parameter may carry. Every caller
# inherits it: the public product search turns these lists into Meilisearch
# ``IN [...]`` filters (categories expanded to their whole subtree), so an
# unbounded list is an unbounded filter. The storefront builds them from the
# filter chips a shopper ticks, one id per chip, plus the listing's own
# category: a real selection is a handful of ids, tens at the very most.
# 100 is well above that and matches ``MAX_POINTS_PREVIEW_PRODUCTS``.
MAX_INT_CSV_ITEMS = 100


def parse_int_csv(
    value: str | None, name: str, *, max_items: int = MAX_INT_CSV_ITEMS
) -> list[int]:
    """Parse a comma-separated list of integers, 400 (not 500) on garbage.

    Also 400 when the list holds more than ``max_items`` entries. The count
    is taken before any value is converted, so an oversized list costs one
    split and nothing more.
    """
    if not value:
        return []
    parts = [v.strip() for v in value.split(",") if v.strip()]
    if len(parts) > max_items:
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
