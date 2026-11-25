"""What a PaymentIntent was created FOR, written on it by the server.

A PaymentIntent proves that money moved, not what it paid for. Every intent
we create carries three server-set metadata keys naming what it pays for, the
thing itself and the tenant. Only the server writes them (Stripe metadata is
set with the secret key at creation), so a shopper holding an intent id
cannot present it for anything else. The webhook finds an order's intent by
them (``OrderService.handle_payment_succeeded``), and a succeeded intent of
ours that nothing carries alerts staff
(``OrderService.report_payment_without_order``).
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


def own_purpose(metadata: Mapping[str, str] | None) -> PaymentPurpose | None:
    """What an intent of this store's own was created for, read from its
    server-set metadata; ``None`` for any other payment: one with no
    binding (a Payment Link, a Stripe Invoice, Terminal, a charge made in
    the Stripe dashboard) or one bound to another tenant."""
    metadata = metadata or {}
    if metadata.get(TENANT_KEY) != getattr(connection, "schema_name", "public"):
        return None
    try:
        return PaymentPurpose(metadata.get(PURPOSE_KEY))
    except ValueError:
        return None
