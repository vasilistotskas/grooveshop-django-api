"""What a PaymentIntent was created FOR, written on it by the server.

A PaymentIntent proves that money moved, not what it paid for. Before this
module an intent carried only an amount and a currency, so one payment could
be presented twice: a Stripe gift-card purchase for exactly a cart's total
bought the card (``payment_intent.succeeded``) and then, with the same
``payment_intent_id`` posted to ``POST /api/v1/order``, a paid order.

Every intent we create now carries three server-set metadata keys, and the
payment-first order path refuses an intent whose keys do not name THIS cart,
this purpose and this tenant. Only the server writes them (Stripe metadata is
set with the secret key at creation), so a shopper holding an intent id
cannot present it for anything else.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from django.db import connection

PURPOSE_KEY = "purpose"
SUBJECT_KEY = "subject"
TENANT_KEY = "tenant_schema"


class PaymentPurpose(StrEnum):
    """What the payment buys; ``SUBJECT_KEY`` names the thing."""

    #: Payment-first checkout; the subject is the cart's uuid.
    CART = "cart"
    #: An order that already exists; the subject is the order's uuid.
    ORDER = "order"
    #: A gift-card purchase; the subject is the purchase's uuid.
    GIFT_CARD = "giftcard"


def binding_metadata(
    purpose: PaymentPurpose, subject: object
) -> dict[str, str]:
    """The metadata every PaymentIntent we create carries."""
    return {
        PURPOSE_KEY: purpose.value,
        SUBJECT_KEY: str(subject),
        TENANT_KEY: getattr(connection, "schema_name", "public"),
    }


def binding_mismatch(
    metadata: Mapping[str, str] | None,
    *,
    purpose: PaymentPurpose,
    subject: object,
) -> str | None:
    """Why *metadata* does not bind its intent to ``purpose``/``subject`` in
    the active tenant; ``None`` when it does.

    The reason names the key that differs, never its value: it ends up in a
    400 body and the value is another shopper's cart uuid.
    """
    metadata = metadata or {}
    expected = binding_metadata(purpose, subject)
    for key, value in expected.items():
        if metadata.get(key) != value:
            return f"the payment intent's {key} does not match this checkout"
    return None
