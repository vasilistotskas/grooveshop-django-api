from enum import StrEnum


class FreeShippingSource(StrEnum):
    """What makes a cart's shipping free: a rate's threshold or a promotion.

    ``THRESHOLD`` is a ``ShippingRate.free_shipping_threshold``, which
    holds for that carrier and kind only; ``PROMOTION`` is a live
    FREE_SHIPPING promotion, which waives every carrier's price.
    """

    THRESHOLD = "threshold"
    PROMOTION = "promotion"
