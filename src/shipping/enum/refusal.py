from enum import StrEnum


class ShippingRefusal(StrEnum):
    """Why a chosen delivery cannot be priced for a cart.

    The stable codes ``POST /cart/quote`` answers with, so a client can
    drop the selection and go on instead of matching translated text.
    ``UNAVAILABLE``: the store does not ship that way to the country
    (no active rate, an inactive carrier, a kind that is switched off).
    """

    UNAVAILABLE = "shipping_unavailable"
    PARCEL_TOO_LARGE = "shipping_parcel_too_large"
    WEIGHT_EXCEEDED = "shipping_weight_exceeded"
