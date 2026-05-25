"""Top-level shipping dispatcher.

Order-flow code, the Order detail serializer, the payment hook, and
``calculate_shipping_cost`` go through this module so they never import
provider apps directly.  The dispatcher resolves the provider via the
DB-backed ``ShippingProvider`` row + the in-memory carrier registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db.models import Q

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider, is_registered
from shipping.models import ShippingProvider

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)


# Defensive cap on free-text notes that ride along to the courier
# voucher. Neither ACS_Create_Voucher nor BoxNow ``deliveryRequest``
# documents a hard limit on the notes/description field, but the
# printed ACS voucher's "Παρατηρήσεις" cell is only a few lines
# wide and BoxNow's partner portal renders the description in a
# fixed-height box; sending 2-3 sentences is helpful, sending a
# novel just truncates ugly. 500 chars is comfortable for either
# side.
DELIVERY_NOTES_MAX_LEN = 500


def sanitize_delivery_notes(value: object) -> str:
    """Return courier-safe free-text notes — trimmed, single-spaced, capped.

    Couriers render this field verbatim on the voucher / partner
    portal. Embedded CRs and tabs make the layout shift
    unpredictably (and a stray ``\\r`` can be interpreted as a
    record separator on ACS's side), so we collapse whitespace runs
    to a single space and trim to ``DELIVERY_NOTES_MAX_LEN``.
    Lives here so both ``shipping_acs`` and ``shipping_boxnow``
    payload builders can pull it without cross-app imports.
    """
    if not value:
        return ""
    text = " ".join(str(value).split())
    return text[:DELIVERY_NOTES_MAX_LEN]


class ShippingService:
    """Provider-agnostic dispatcher used by the rest of the codebase."""

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def adapter_for(cls, provider_code: str):
        """Return the registered carrier adapter for ``provider_code``."""
        return get_provider(provider_code)

    @classmethod
    def adapter_for_order(cls, order: Order):
        """Return the adapter attached to ``order`` or None.

        Falls back to None when the order has no shipping provider FK or
        the provider's adapter is not registered (e.g. its app is
        disabled in this deploy).
        """
        provider = getattr(order, "shipping_provider", None)
        if provider is None or not is_registered(provider.code):
            return None
        return get_provider(provider.code)

    # ------------------------------------------------------------------
    # Create / cancel / labels
    # ------------------------------------------------------------------

    @classmethod
    def create_shipment_for_order(
        cls,
        order: Order,
        *,
        payload: dict[str, Any] | None = None,
    ):
        """Dispatch shipment creation to the order's provider adapter.

        Returns the adapter's shipment row; returns None when the order
        has no provider attached (legacy rows pre-Phase-0 migration).
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        kind = ShippingKind(order.shipping_kind)
        return adapter.create_shipment(order, kind=kind, payload=payload or {})

    @classmethod
    def create_shipment_row_for_order(
        cls,
        order: Order,
        *,
        payload: dict[str, Any] | None = None,
        items: list[tuple[Any, int]] | None = None,
    ) -> None:
        """Persist the provider's shipment row at order-creation time.

        Idempotent and a no-op when the order has no provider attached
        or the provider's adapter does not implement
        ``create_shipment_row``.

        Used by the three ``OrderService.create_order*`` paths so each
        path collapses from a per-provider if/elif tower into one
        registry-dispatched call.
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        kind = ShippingKind(order.shipping_kind)
        adapter.create_shipment_row(
            order, kind=kind, payload=payload or {}, items=items
        )

    @classmethod
    def dispatch_create_shipment_task(cls, order: Order) -> None:
        """Fire the provider's create-shipment Celery task.

        The order MUST have ``shipping_provider`` set — orders created
        through any of the ``OrderService.create_order*`` paths always
        go through ``_resolve_shipping_provider`` which sets it. A
        missing provider here means the order is genuinely
        provider-less (e.g. flat-rate home delivery without a courier
        adapter) — silently return.

        Defers the per-carrier dispatch with ``transaction.on_commit``
        so the Celery worker never receives the order_id before the
        creating transaction has committed. Without this guard the
        worker observed a "Order N not found" race (verified on prod
        order 47, 2026-04-30): the task fired inside the open
        ``create_order_from_cart_offline`` transaction, the worker
        looked up the order on a different DB connection, and gave up
        permanently because Order.DoesNotExist isn't a retryable
        exception. ``on_commit`` is a no-op outside a transaction, so
        the admin "issue voucher now" action and other already-
        committed call sites still dispatch immediately.
        """
        from django.db import transaction

        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return
        transaction.on_commit(
            lambda: adapter.dispatch_create_shipment_task(order)
        )

    @classmethod
    def cancel_shipment(cls, order: Order, *, reason: str = "") -> bool:
        """Cancel the order's shipment. Returns True when dispatched."""
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return False
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return False
        adapter.cancel_shipment(shipment, reason=reason)
        return True

    @classmethod
    def fetch_label_bytes(cls, order: Order) -> bytes | None:
        """Return the label PDF for ``order`` or None when unavailable."""
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return None
        return adapter.fetch_label_bytes(shipment)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @classmethod
    def serialize_shipment(
        cls,
        order: Order,
        *,
        context: dict | None = None,
    ) -> dict | None:
        """Return the adapter's detail-serializer dict for ``order``."""
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return None
        return adapter.serialize_shipment(shipment, context=context or {})

    # ------------------------------------------------------------------
    # Validation (used by order/services._validate_address_data)
    # ------------------------------------------------------------------

    @classmethod
    def validate_order_payload(
        cls,
        *,
        provider_code: str,
        kind: ShippingKind | str,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Delegate validation to the provider adapter (DRF errors-dict)."""
        adapter = cls.adapter_for(provider_code)
        kind_enum = (
            kind if isinstance(kind, ShippingKind) else ShippingKind(kind)
        )
        return adapter.validate_order_payload(kind=kind_enum, payload=payload)

    # ------------------------------------------------------------------
    # Available options (for /api/v1/shipping/options)
    # ------------------------------------------------------------------

    @classmethod
    def available_options(
        cls,
        *,
        country_code: str | None = None,
        order_value_amount: float = 0.0,
        currency: str = "EUR",
        weight_grams: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the matrix of (provider, kind) options for checkout.

        Filters by:
        * ``ShippingProvider.is_active = True``
        * Provider must have an adapter registered (so deployments
          missing a provider app don't surface its options).
        * ``country_code`` filter against the provider's
          ``metadata['supported_countries']`` list when present.
        """
        qs = ShippingProvider.objects.filter(is_active=True).filter(
            Q(supports_home_delivery=True) | Q(supports_pickup_point=True)
        )

        options: list[dict[str, Any]] = []
        for provider in qs:
            if not is_registered(provider.code):
                logger.warning(
                    "Active ShippingProvider '%s' has no registered adapter "
                    "— skipping in available_options()",
                    provider.code,
                )
                continue

            supported_countries = (provider.metadata or {}).get(
                "supported_countries"
            )
            if (
                country_code
                and supported_countries
                and country_code.upper() not in supported_countries
            ):
                continue

            adapter = get_provider(provider.code)

            for kind, supported in (
                (ShippingKind.HOME_DELIVERY, provider.supports_home_delivery),
                (ShippingKind.PICKUP_POINT, provider.supports_pickup_point),
            ):
                if not supported:
                    continue
                # Per-kind feature flag (e.g. ACS_SMARTPOINT_ENABLED)
                # — a provider can advertise capability via the
                # ShippingProvider row but gate user-facing visibility
                # on a Setting it controls itself.
                if not adapter.is_kind_enabled(kind):
                    continue
                price = adapter.calculate_shipping_cost(
                    order_value_amount=order_value_amount,
                    currency=currency,
                    kind=kind,
                    weight_grams=weight_grams,
                )
                # Per-(provider, kind) logo: ``pickup_point`` rows
                # prefer ``logo_pickup_point`` when uploaded so a
                # carrier can have a distinct locker illustration vs
                # its home-delivery brand mark (e.g. ACS home
                # delivery vs ACS Smartpoint). The model handles the
                # fallback chain; this view just consumes the
                # resolved URL.
                options.append(
                    {
                        "provider_code": provider.code,
                        "provider_name": provider.name,
                        "kind": kind.value,
                        "price": price[0] if price else None,
                        "currency": price[1] if price else currency,
                        "live_mode": provider.live_mode,
                        "priority": provider.priority,
                        "logo_url": provider.logo_url_for_kind(kind.value),
                        "metadata": provider.metadata or {},
                    }
                )

        options.sort(key=lambda opt: (opt["priority"], opt["provider_code"]))
        return options

    # ------------------------------------------------------------------
    # Free-shipping advertising
    # ------------------------------------------------------------------

    @classmethod
    def free_shipping_info(
        cls,
        *,
        currency: str | None = None,
        country_code: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate per-(active provider, kind) free-shipping thresholds.

        Powers ``GET /api/v1/shipping/free-shipping-info`` which the
        storefront reads to render "Δωρεάν μεταφορικά άνω των X €" on
        the product detail page and the cart summary. The semantics
        keep marketing honest:

        * ``min_threshold`` is the earliest cart subtotal at which at
          least one carrier+kind ships free — i.e. the headline number
          we want to advertise; matches the "from X €" mental model.
        * ``max_threshold`` is the subtotal at which **every** active
          carrier+kind ships free — useful for the cart page's "free
          shipping unlocked" copy.
        * ``providers`` carries the per-row breakdown so the checkout
          summary can show carrier-specific badges if we want to in
          the future without another round-trip.

        Filters mirror :meth:`available_options`:
        * ``ShippingProvider.is_active`` must be True.
        * The provider's adapter must be registered (so a deploy
          missing a provider app doesn't surface stale rows).
        * ``country_code`` matches against the provider's
          ``metadata['supported_countries']`` list when present.
        * The carrier's ``is_kind_enabled(kind)`` hook lets a provider
          gate a kind independently (e.g. ACS Smartpoint hidden
          until ops flip ``ACS_SMARTPOINT_ENABLED``).
        * Rows where the adapter returns ``None`` from
          ``free_shipping_threshold`` are skipped — a missing
          threshold is NOT the same as "free at €0".
        """
        active_currency = currency or settings.DEFAULT_CURRENCY

        qs = ShippingProvider.objects.filter(is_active=True).filter(
            Q(supports_home_delivery=True) | Q(supports_pickup_point=True),
        )

        providers: list[dict[str, Any]] = []
        for provider in qs:
            if not is_registered(provider.code):
                logger.warning(
                    "Active ShippingProvider '%s' has no registered adapter"
                    " — skipping in free_shipping_info()",
                    provider.code,
                )
                continue

            supported_countries = (provider.metadata or {}).get(
                "supported_countries"
            )
            if (
                country_code
                and supported_countries
                and country_code.upper() not in supported_countries
            ):
                continue

            adapter = get_provider(provider.code)

            for kind, supported in (
                (ShippingKind.HOME_DELIVERY, provider.supports_home_delivery),
                (ShippingKind.PICKUP_POINT, provider.supports_pickup_point),
            ):
                if not supported or not adapter.is_kind_enabled(kind):
                    continue
                threshold = adapter.free_shipping_threshold(kind)
                if threshold is None:
                    continue
                providers.append(
                    {
                        "provider_code": provider.code,
                        "provider_name": provider.name,
                        "kind": kind.value,
                        "threshold": threshold,
                        "priority": provider.priority,
                    }
                )

        providers.sort(key=lambda row: (row["priority"], row["provider_code"]))

        thresholds = [row["threshold"] for row in providers]
        return {
            "providers": providers,
            "min_threshold": min(thresholds) if thresholds else None,
            "max_threshold": max(thresholds) if thresholds else None,
            "currency": active_currency,
        }

    # ------------------------------------------------------------------
    # Pricing dispatcher
    # ------------------------------------------------------------------

    @classmethod
    def calculate_shipping_cost(
        cls,
        *,
        provider_code: str | None,
        kind: str,
        order_value_amount: float,
        currency: str,
        country_id: str | None = None,
        region_id: str | None = None,
        weight_grams: int | None = None,
    ) -> tuple[float, str] | None:
        """Return the shipping price for a provider+kind combination.

        Returns None when the provider has no opinion — caller falls
        back to the global flat rate.
        """
        if not provider_code or not is_registered(provider_code):
            logger.debug(
                "ShippingService.calculate_shipping_cost: no adapter for "
                "provider_code=%r — returning None (caller will use "
                "generic fallback)",
                provider_code,
            )
            return None
        adapter = get_provider(provider_code)
        kind_enum = ShippingKind(kind)
        quote = adapter.calculate_shipping_cost(
            order_value_amount=order_value_amount,
            currency=currency,
            kind=kind_enum,
            country_id=country_id,
            region_id=region_id,
            weight_grams=weight_grams,
        )
        # Anchor log for the per-carrier pricing decision. Used by ops
        # to answer "did the free-shipping threshold fire for this
        # cart?" without re-running the carrier adapter under a
        # debugger. ``extra={}`` keys mirror the structured logging
        # used in OrderService so a single query joins both halves of
        # the calc trail.
        if quote is not None:
            amount, _ = quote
            logger.info(
                "Shipping quote: provider=%s kind=%s order_value=%.2f %s "
                "weight_grams=%s -> %.2f %s",
                provider_code,
                kind,
                order_value_amount,
                currency,
                weight_grams,
                amount,
                currency,
                extra={
                    "shipping_provider_code": provider_code,
                    "shipping_kind": kind,
                    "order_value_amount": order_value_amount,
                    "currency": currency,
                    "weight_grams": weight_grams,
                    "country_id": country_id,
                    "region_id": region_id,
                    "shipping_amount": amount,
                    "is_free": amount == 0.0,
                },
            )
        return quote
