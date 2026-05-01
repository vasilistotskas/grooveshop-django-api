"""Standard event names + content_type constants for the Meta CAPI.

Pulled into one module so call sites import a typed constant rather
than a stringly-typed event name. Names match Meta's standard event
catalogue verbatim — do NOT customise; using anything other than the
exact spelling kills attribution because Meta won't match against
the corresponding browser-pixel event.
"""

from __future__ import annotations

from enum import StrEnum


class StandardEvent(StrEnum):
    """Meta-defined standard event names.

    Reference: https://developers.facebook.com/docs/meta-pixel/reference
    """

    PURCHASE = "Purchase"
    INITIATE_CHECKOUT = "InitiateCheckout"
    ADD_TO_CART = "AddToCart"
    ADD_PAYMENT_INFO = "AddPaymentInfo"
    VIEW_CONTENT = "ViewContent"
    SEARCH = "Search"
    COMPLETE_REGISTRATION = "CompleteRegistration"
    LEAD = "Lead"
    SUBSCRIBE = "Subscribe"


class ContentType(StrEnum):
    """``custom_data.content_type`` values."""

    PRODUCT = "product"
    PRODUCT_GROUP = "product_group"


# Event keys we persist on ``order.metadata['meta']['event_ids']`` so
# the same event_id is reused between the browser pixel firing on the
# success page and the server-side dispatch. Kept here to keep the
# allow-list narrow — random keys typed elsewhere won't be matched.
META_EVENT_ID_KEYS: tuple[str, ...] = (
    "purchase",
    "initiate_checkout",
    "add_payment_info",
)
