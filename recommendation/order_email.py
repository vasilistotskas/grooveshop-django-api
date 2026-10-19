"""The ``order_email`` surface: suggestions in the order confirmation.

Seeded with the order's own products, so the strip is "goes with what
you just bought", and excluding them. Rendered by the confirmation
templates through ``emails/order/_suggestions.html`` / ``.txt``.

No prices: the email is rendered without a request, so the viewer's
pricing context (a B2B customer group) cannot be applied, and a retail
price shown to a wholesale customer would be wrong. The product page
the link opens prices it correctly.

The email IS the showing, so its impression is recorded when it is
sent (``record_impression``), with the order's customer and cart as the
identities a later order is matched on (``events.record_attach_events``)
— the same rows the storefront strips write, under their own surface,
so the nightly weight learning measures this surface like any other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.utils.tenant_urls import get_tenant_frontend_url
from recommendation.context import recommendations_active
from recommendation.engine import Suggestion, SuggestionContext, suggest
from recommendation.enum import EventKind, Surface

if TYPE_CHECKING:
    from order.models.order import Order


@dataclass(frozen=True, slots=True)
class EmailSuggestion:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class EmailStrip:
    impression_id: uuid.UUID
    suggestions: list[Suggestion]
    items: list[EmailSuggestion]


def order_email_strip(order: Order, *, language: str) -> EmailStrip | None:
    """The strip for ``order``'s confirmation, or None when there is none.

    Call inside ``translation.override(language)``: the names are read
    in the active language, the links built for ``language``.
    """
    from product.models import Product

    if not recommendations_active():
        return None
    seed_ids = tuple(
        dict.fromkeys(order.items.values_list("product_id", flat=True))
    )
    suggestions = suggest(
        SuggestionContext(surface=Surface.ORDER_EMAIL, seed_ids=seed_ids)
    )
    if not suggestions:
        return None
    products = Product.objects.with_translations().in_bulk(
        [s.product_id for s in suggestions]
    )
    shown = [s for s in suggestions if s.product_id in products]
    if not shown:
        return None
    return EmailStrip(
        impression_id=uuid.uuid4(),
        suggestions=shown,
        items=[
            EmailSuggestion(
                name=products[s.product_id].safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                url=get_tenant_frontend_url(
                    products[s.product_id].storefront_path, language=language
                ),
            )
            for s in shown
        ],
    )


def record_impression(order: Order, strip: EmailStrip) -> None:
    """Record the sent strip as an impression, off the email's path."""
    from recommendation.tasks import record_recommendation_event

    snapshot = (order.metadata or {}).get("cart_snapshot") or {}
    record_recommendation_event.delay(
        kind=EventKind.IMPRESSION,
        surface=Surface.ORDER_EMAIL,
        impression_id=str(strip.impression_id),
        items=[
            {
                "product_id": suggestion.product_id,
                "strategy": suggestion.strategy,
                "position": position,
            }
            for position, suggestion in enumerate(strip.suggestions)
        ],
        user_id=order.user_id,
        cart_uuid=snapshot.get("cart_uuid"),
    )
