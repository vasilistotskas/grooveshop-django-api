"""Signal receivers that translate domain events into CAPI dispatches.

Hooks (intentionally narrow):
* ``order.signals.order_paid``      → Purchase (online payments — the
  provider webhook confirms the charge seconds after checkout)
* ``order.signals.order_created``   → InitiateCheckout (server-leg),
  plus Purchase for OFFLINE pay-ways (COD): the confirmation page is
  the conversion — no webhook ever fires at purchase time (the ACS
  COD reconcile emits ``order_paid`` days later at cash remittance,
  and BoxNow COD has no reconcile at all). The event-log's unique
  ``event_id`` + SENT short-circuit make any later ``order_paid``
  re-dispatch a no-op, so both hooks can safely coexist.
* ``order.signals.order_refunded``  → Refund (custom)
* ``allauth.account.signals.user_signed_up`` → CompleteRegistration
"""

from __future__ import annotations

import logging
from typing import Any

from allauth.account.signals import user_signed_up
from django.dispatch import receiver
from djmoney.money import Money

from core.client_ip import edge_client_ip
from meta_capi.tasks import (
    dispatch_complete_registration_event,
    schedule_initiate_checkout,
    schedule_purchase,
    schedule_refund,
)
from order.signals import order_created, order_paid, order_refunded
from pay_way.enum.settlement import PaySettlement

logger = logging.getLogger(__name__)


@receiver(order_paid, dispatch_uid="meta_capi.send_purchase")
def _on_order_paid(sender: Any, order: Any, **kwargs: Any) -> None:
    schedule_purchase(order.id)


@receiver(order_created, dispatch_uid="meta_capi.send_initiate_checkout")
def _on_order_created(sender: Any, order: Any, **kwargs: Any) -> None:
    schedule_initiate_checkout(order.id)

    # Offline pay-ways (COD): dispatch Purchase at confirmation time —
    # matching the browser pixel leg on the success page so Meta
    # dedups the pair via the shared event_id within its window.
    # Consent + kill-switch gating happens inside the task
    # (``should_dispatch_for_order``), so admin-created orders and
    # consent-declined shoppers are excluded there.
    pay_way = getattr(order, "pay_way", None)
    if (
        pay_way is not None
        and PaySettlement(pay_way.settlement) != PaySettlement.ONLINE
    ):
        logger.debug(
            "Scheduling offline-payway Purchase for order %s", order.id
        )
        schedule_purchase(order.id)


@receiver(order_refunded, dispatch_uid="meta_capi.send_refund")
def _on_order_refunded(
    sender: Any, order: Any, amount: Money, **kwargs: Any
) -> None:
    schedule_refund(order.id, amount.amount)


@receiver(user_signed_up, dispatch_uid="meta_capi.send_complete_registration")
def _on_user_signed_up(
    sender: Any, request: Any, user: Any, **kwargs: Any
) -> None:
    """allauth fires this for every signup path (email, social).

    The request gives us the UA, the IP when our edge vouched for it, and
    the _fbp / _fbc cookies we need for accurate matching. Without
    those, EMQ on registration drops enough that the event is barely
    useful — so we read them here rather than letting the Celery task
    figure it out cold.
    """
    # Gate on ad-storage consent: without an explicit "granted" signal from
    # the signup request we must NOT send the registrant's PII to Meta
    # (G0199). Mirrors the order path (``should_dispatch_for_order`` →
    # ``_consent_granted``) and the browser pixel, which both gate on the
    # storefront's ``ad_storage`` consent (Google Consent Mode v2). Fail
    # closed: no request (e.g. programmatic signup) means no consent signal,
    # so we skip the dispatch entirely.
    if request is None:
        return

    cookies = getattr(request, "COOKIES", {}) or {}
    if (cookies.get("ad_storage") or "").strip().lower() != "granted":
        return

    fbp = cookies.get("_fbp", "") or ""
    fbc = cookies.get("_fbc", "") or ""

    meta = getattr(request, "META", {}) or {}
    # The visitor our Cloudflare edge vouched for, or no IP at all. Without
    # ``X-Origin-Verify`` the address is the caller's choice (Nuxt relays
    # its ``CF-Connecting-IP`` as ``X-Real-IP``), and the peer
    # ``client_ip_or_peer`` would fall back to is a proxy. Meta asks for the
    # browser's real address and does not require one, so it is omitted.
    ip_addr = edge_client_ip(request)
    ua = meta.get("HTTP_USER_AGENT", "") or ""
    # ``HTTP_REFERER`` is the storefront page that submitted the signup
    # form — Meta uses it for content matching.
    event_source_url = meta.get("HTTP_REFERER", "") or ""

    # Wrapped in on_commit so the worker sees the committed user row.
    # The allauth signup flow runs inside an atomic block; dispatching
    # before commit means the worker can hit User.DoesNotExist on a
    # fast Celery node.
    from tenant.celery import dispatch_on_commit

    dispatch_on_commit(
        dispatch_complete_registration_event,
        [user.id],
        {
            "fbp": fbp or None,
            "fbc": fbc or None,
            "client_ip_address": ip_addr,
            "client_user_agent": ua or None,
            "event_source_url": event_source_url or None,
        },
    )
