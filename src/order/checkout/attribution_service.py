"""Recording where an order came from: the storefront's first-touch
capture classified for the store (``order.checkout.attribution`` holds the
pure classifier) and stored on ``OrderAttribution``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from order.checkout.attribution import (
    AttributionInput,
    ClassifiedAttribution,
    classify,
)
from order.models.attribution import OrderAttribution
from order.models.order import (
    Order,
)
from tenant.internal import is_internal_caller
from tenant.membership import bound_tenant
from tenant.middleware import tenant_domain_set

if TYPE_CHECKING:
    from cart.models import Cart


class OrderAttributionService:
    @staticmethod
    def input_from_request(
        request, payload: Mapping[str, Any] | None
    ) -> AttributionInput:
        """The storefront's first-touch capture plus the shopper's UA.

        Shared by every endpoint that takes the capture (order create,
        add to cart). The UA is the shopper's own: the Nuxt proxy relays
        it (``clientIdentityHeaders``), and an in-app browser that strips
        ``Referer`` still names itself there. The gateway's
        ``agentProtocol`` is believed only alongside its shared secret,
        which it sends as ``X-Internal-Gateway`` on every cart and order
        call (the header the gateway-aware cart throttle checks too).
        """
        return AttributionInput.from_payload(
            payload,
            user_agent=request.headers.get("User-Agent", ""),
            from_agent_gateway=is_internal_caller(
                request.headers.get("X-Internal-Gateway", "")
            ),
        )

    @staticmethod
    def classify_for_store(
        attribution: AttributionInput,
    ) -> ClassifiedAttribution:
        """Name the channel behind *attribution*. A referrer on one of
        the store's own domains is an internal navigation or a return
        from checkout, never a source."""
        return classify(
            attribution,
            own_hosts=tenant_domain_set(bound_tenant()),
        )

    @classmethod
    def record(
        cls,
        order: Order,
        attribution: AttributionInput,
        *,
        cart: Cart | None = None,
    ) -> OrderAttribution:
        """Classify where *order* came from and store it.

        Every new order gets a row — ``direct`` when there was no signal
        at all — so "no row" keeps meaning "placed before attribution
        existed". ``cart`` is the cart the order is built from: its
        first-touch source is copied next to the checkout source,
        because the cart is deleted once its lines become an order.
        """
        from cart.models import CartAttribution

        classified = cls.classify_for_store(attribution)
        cart_source = (
            CartAttribution.objects.filter(pk=cart.pk).first()
            if cart is not None
            else None
        )
        return OrderAttribution.objects.create(
            order=order,
            cart_source_type=cart_source.source_type if cart_source else "",
            cart_source=cart_source.source if cart_source else "",
            source_type=classified.source_type,
            source=classified.source,
            medium=classified.medium,
            campaign=classified.campaign,
            referrer_host=classified.referrer_host,
            landing_path=classified.landing_path,
        )
