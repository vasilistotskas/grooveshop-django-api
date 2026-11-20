"""The cart a request addresses by its ``X-Cart-Id`` header.

In ``core`` because the cart lookup, the idempotency middleware and the
throttles must all read the header one way: a cart is one cart however
its uuid is spelled, so a budget or a replay key built from the raw
header would hand one cart a fresh one per spelling.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request


def cart_uuid_from_request(request: Request | HttpRequest) -> uuid.UUID | None:
    """The cart the caller is addressing, from the ``X-Cart-Id`` header.

    Carts are addressed by their unguessable UUID, never the sequential
    PK — otherwise any anonymous caller could enumerate other guests'
    carts by incrementing an integer header (IDOR). Anything that is
    not a valid UUID reads as "no cart". The storefront proxy sends the
    header on every cart-scoped call, including the recommendation
    feedback events, so the same parse serves both.
    """
    if hasattr(request, "META"):
        raw = request.META.get("HTTP_X_CART_ID")
    else:
        raw = request.headers.get("X-Cart-Id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError, TypeError, AttributeError:
        return None
