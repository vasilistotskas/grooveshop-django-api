"""Tenant-context helpers for dj-stripe webhook receivers.

Stripe webhooks are HOST-ROUTED: each tenant's endpoint lives on its own
API domain (``https://<tenant-api-domain>/stripe/webhook/<uuid>/``), so
``TenantMainMiddleware`` has already selected the tenant schema before
dj-stripe verifies and processes the event. Every Stripe object we
create is additionally stamped with ``metadata.tenant_schema`` inside
``order/payment.py`` — these helpers extract that value and re-enter the
schema explicitly, which keeps the handlers correct as defense in depth
(and for any event replayed outside a tenant request context).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable

from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)


def _claimed_schema_from_event(event) -> str:
    """Read the tenant schema CLAIMED by the event's Stripe metadata.

    This is untrusted input. ``metadata`` is set by whoever created the
    Stripe object, which for a tenant's own account is that tenant's
    operator — so the value proves only what the sender asserts, never
    which tenant the event belongs to. Use it to cross-check the
    host-routed schema (see ``_resolve_event_schema``), never to select
    one.

    Search order (first non-empty wins):
    1. ``event.data.object.metadata.tenant_schema``  — PaymentIntent events
    2. ``event.data.object.payment_intent.metadata.tenant_schema``  — expanded
       PI on Charge/Dispute events where the PI is embedded
    3. ``event.data.metadata.tenant_schema`` — top-level fallback
    """
    try:
        data = event.data if hasattr(event, "data") else {}
        obj = data.get("object") if hasattr(data, "get") else None

        # 1. Direct object metadata (PaymentIntent, CheckoutSession)
        if isinstance(obj, dict):
            schema = (obj.get("metadata") or {}).get("tenant_schema") or ""
            if schema:
                return schema.strip()

            # 2. Nested payment_intent metadata (Charge events, Disputes)
            pi = obj.get("payment_intent")
            if isinstance(pi, dict):
                schema = (pi.get("metadata") or {}).get("tenant_schema") or ""
                if schema:
                    return schema.strip()

        # 3. Top-level event data metadata
        if hasattr(data, "get"):
            schema = (data.get("metadata") or {}).get("tenant_schema") or ""
            if schema:
                return schema.strip()

        return ""
    except Exception:
        logger.warning(
            "Could not read tenant_schema metadata from Stripe event %s",
            getattr(event, "id", "unknown"),
            exc_info=True,
        )
        return ""


def _resolve_event_schema(event) -> str | None:
    """Decide which schema a Stripe event may act on, or ``None`` to skip.

    Stripe webhooks are HOST-ROUTED: every tenant owns a
    ``WebhookEndpoint`` row on its own API domain, with its own signing
    secret, and ``TenantMainMiddleware`` selects the schema from that
    host before dj-stripe verifies the signature. The receiving schema
    is therefore the one fact about an event that the sender cannot
    influence, and it is authoritative.

    ``metadata.tenant_schema`` is NOT. It is attacker-controlled for any
    merchant holding their own Stripe account: create a €0.50 Checkout
    Session in your own account carrying another tenant's schema and
    order id, pay it, and the event arrives validly signed by YOUR
    endpoint secret. Selecting the schema from that field let one
    merchant mark another merchant's orders paid — and the commit hooks
    would mint a real courier shipment for goods nobody paid for.

    Rules:
    - Tenant host (non-public schema): that schema wins. If metadata
      claims a different one, the event is refused and logged at ERROR —
      a mismatch is either an attack or a genuinely mis-stamped object,
      and neither should mutate data.
    - Public schema (platform host, replay outside a request, or an
      event processed before the per-tenant endpoints were cut over):
      there is no host to trust, so fall back to the claimed schema.
      This cannot be abused to cross tenants, because a request that
      arrived on a tenant's own endpoint never resolves to public.
    """
    request_schema = getattr(connection, "schema_name", None) or (
        get_public_schema_name()
    )
    claimed = _claimed_schema_from_event(event)

    if request_schema != get_public_schema_name():
        if claimed and claimed != request_schema:
            logger.error(
                "Stripe event %s arrived on schema %r but its metadata "
                "claims %r — refusing to process. A merchant cannot act "
                "on another tenant's data via metadata.",
                getattr(event, "id", "unknown"),
                request_schema,
                claimed,
            )
            return None
        return request_schema

    return claimed or request_schema


def with_tenant_schema_from_event(func: Callable) -> Callable:
    """Decorator that wraps a ``@djstripe_receiver`` body in the correct
    tenant ``schema_context``.

    Usage::

        @djstripe_receiver("payment_intent.succeeded")
        @with_tenant_schema_from_event
        def handle_stripe_payment_succeeded(sender, **kwargs):
            ...

    If the resolved schema does not correspond to an active Tenant row the
    decorator logs a warning and returns early rather than crashing — a
    misconfigured or deleted tenant should not prevent other events from
    processing.
    """

    @functools.wraps(func)
    def wrapper(sender, **kwargs):
        event = kwargs.get("event")
        if event is None:
            return func(sender, **kwargs)

        schema_name = _resolve_event_schema(event)

        # None means the event's metadata contradicted the host it
        # arrived on — already logged at ERROR. Skip rather than guess.
        if schema_name is None:
            return None

        # Validate that the schema exists before entering it.  An unknown
        # schema_context raises a ProgrammingError which would bubble up as
        # a 500 and cause Stripe to redeliver indefinitely.
        if schema_name != get_public_schema_name():
            try:
                from tenant.models import Tenant

                # Mirror the Viva/BoxNow resolvers: a suspended tenant's
                # Stripe events must not mutate its frozen data. Stripe
                # will redeliver until the operator reactivates or the
                # event ages out.
                tenant = Tenant.objects.filter(
                    schema_name=schema_name,
                    is_active=True,
                    suspended_at__isnull=True,
                ).first()
            except Exception:
                tenant = None

            if tenant is None:
                logger.warning(
                    "Stripe event %s references unknown/inactive tenant "
                    "schema %r — skipping handler %s",
                    getattr(event, "id", "unknown"),
                    schema_name,
                    func.__name__,
                )
                return None

            # tenant_context (not schema_context): the latter binds a
            # bare FakeTenant to the connection, and every helper in
            # tenant/credentials.py reads real fields off
            # ``connection.tenant`` — under a FakeTenant they all return
            # "", so a handler that instantiates a payment provider
            # would raise ImproperlyConfigured. The Viva and Celery
            # resolvers already do this deliberately; this path was the
            # odd one out.
            from django_tenants.utils import tenant_context

            with tenant_context(tenant):
                return func(sender, **kwargs)

        with schema_context(schema_name):
            return func(sender, **kwargs)

    return wrapper
