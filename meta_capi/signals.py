"""Signal receivers that translate domain events into CAPI dispatches.

Hooks (intentionally narrow):
* ``order.signals.order_paid``      → Purchase
* ``order.signals.order_created``   → InitiateCheckout (server-leg)
* ``order.signals.order_refunded``  → Refund (custom)
* ``allauth.account.signals.user_signed_up`` → CompleteRegistration

The Purchase hook is the load-bearing one: it fires from BOTH the
Stripe ``payment_intent.succeeded`` webhook AND the COD offline
order path, so we don't have to touch payment-provider-specific code
to cover both flows.
"""

from __future__ import annotations

import logging
from typing import Any

from allauth.account.signals import user_signed_up
from django.dispatch import receiver

from meta_capi.tasks import (
    dispatch_complete_registration_event,
    schedule_initiate_checkout,
    schedule_purchase,
    schedule_refund,
)
from order.signals import order_created, order_paid, order_refunded

logger = logging.getLogger(__name__)


@receiver(order_paid, dispatch_uid="meta_capi.send_purchase")
def _on_order_paid(sender: Any, order: Any, **kwargs: Any) -> None:
    schedule_purchase(order.id)


@receiver(order_created, dispatch_uid="meta_capi.send_initiate_checkout")
def _on_order_created(sender: Any, order: Any, **kwargs: Any) -> None:
    schedule_initiate_checkout(order.id)


@receiver(order_refunded, dispatch_uid="meta_capi.send_refund")
def _on_order_refunded(
    sender: Any, order: Any, amount: Any | None = None, **kwargs: Any
) -> None:
    schedule_refund(order.id, amount)


@receiver(user_signed_up, dispatch_uid="meta_capi.send_complete_registration")
def _on_user_signed_up(
    sender: Any, request: Any, user: Any, **kwargs: Any
) -> None:
    """allauth fires this for every signup path (email, social).

    The request gives us the IP/UA + the _fbp / _fbc cookies we need
    for accurate matching. Without those, EMQ on registration drops
    enough that the event is barely useful — so we read them here
    rather than letting the Celery task figure it out cold.
    """
    fbp = ""
    fbc = ""
    ip_addr = ""
    ua = ""
    event_source_url = ""

    if request is not None:
        cookies = getattr(request, "COOKIES", {}) or {}
        fbp = cookies.get("_fbp", "") or ""
        fbc = cookies.get("_fbc", "") or ""

        meta = getattr(request, "META", {}) or {}
        # Production: Cloudflare → Traefik → Nuxt → Django. The Nuxt
        # ``createHeaders`` helper resolves the real client IP from
        # ``CF-Connecting-IP`` and forwards it on ``X-Real-IP`` (the
        # same header allauth uses via ``ALLAUTH_TRUSTED_CLIENT_IP_
        # HEADER``). Trusting X-Real-IP from any other path would be
        # spoofable, but the Nuxt internal cluster call is the only
        # ingress that reaches Django at this stage of the signup
        # flow, and ``USE_X_FORWARDED_HOST`` already pins the trust
        # boundary at the proxy.
        ip_addr = (meta.get("HTTP_X_REAL_IP") or "").strip()
        if not ip_addr:
            forwarded_for = (meta.get("HTTP_X_FORWARDED_FOR") or "").strip()
            if forwarded_for:
                ip_addr = forwarded_for.split(",")[0].strip()
            else:
                ip_addr = meta.get("REMOTE_ADDR", "") or ""
        ua = meta.get("HTTP_USER_AGENT", "") or ""
        # ``HTTP_REFERER`` is the storefront page that submitted the
        # signup form — Meta uses it for content matching.
        event_source_url = meta.get("HTTP_REFERER", "") or ""

    dispatch_complete_registration_event.delay(
        user.id,
        fbp=fbp or None,
        fbc=fbc or None,
        client_ip_address=ip_addr or None,
        client_user_agent=ua or None,
        event_source_url=event_source_url or None,
    )
