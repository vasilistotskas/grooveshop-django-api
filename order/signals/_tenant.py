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
from typing import Callable

from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)


def _tenant_schema_from_event(event) -> str:
    """Extract the target tenant schema from a dj-stripe Event.

    Search order (first non-empty wins):
    1. ``event.data.object.metadata.tenant_schema``  — PaymentIntent events
    2. ``event.data.object.payment_intent.metadata.tenant_schema``  — expanded
       PI on Charge/Dispute events where the PI is embedded
    3. ``event.data.metadata.tenant_schema`` — top-level fallback
    4. The CURRENT connection schema — dashboard-initiated objects
       (manual refunds, disputes) carry no tenant_schema metadata, but
       the webhook arrived on the tenant's own host-routed endpoint, so
       the schema the middleware already selected is authoritative.
       Bouncing these to public would silently skip the handler.
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

        # 4. Host-routed fallback: trust the schema the middleware
        # selected for this request.
        return getattr(connection, "schema_name", None) or (
            get_public_schema_name()
        )
    except Exception:
        logger.warning(
            "Could not extract tenant_schema from Stripe event %s",
            getattr(event, "id", "unknown"),
            exc_info=True,
        )
        return getattr(connection, "schema_name", None) or (
            get_public_schema_name()
        )


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

        schema_name = _tenant_schema_from_event(event)

        # Validate that the schema exists before entering it.  An unknown
        # schema_context raises a ProgrammingError which would bubble up as
        # a 500 and cause Stripe to redeliver indefinitely.
        if schema_name != get_public_schema_name():
            try:
                from tenant.models import Tenant  # noqa: PLC0415

                # Mirror the Viva/BoxNow resolvers: a suspended tenant's
                # Stripe events must not mutate its frozen data. Stripe
                # will redeliver until the operator reactivates or the
                # event ages out.
                exists = Tenant.objects.filter(
                    schema_name=schema_name,
                    is_active=True,
                    suspended_at__isnull=True,
                ).exists()
            except Exception:
                exists = False

            if not exists:
                logger.warning(
                    "Stripe event %s references unknown/inactive tenant "
                    "schema %r — skipping handler %s",
                    getattr(event, "id", "unknown"),
                    schema_name,
                    func.__name__,
                )
                return None

        with schema_context(schema_name):
            return func(sender, **kwargs)

    return wrapper
