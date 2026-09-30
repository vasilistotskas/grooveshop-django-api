"""Top-level shipping dispatcher.

Order-flow code, the Order detail serializer, the payment hook, and
``quote`` go through this module so they never import provider apps
directly. The dispatcher resolves the provider via the DB-backed
``ShippingProvider`` row + the in-memory carrier registry, and the
price/availability/weight-cap via the DB-backed ``ShippingRate`` row.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db.models import Q
from djmoney.money import Money

from shipping.enum import ShippingKind
from shipping.exceptions import (
    ShippingUnavailableError,
    ShippingWeightExceededError,
)
from shipping.interfaces import get_provider, is_registered
from shipping.models import ShippingProvider, ShippingRate

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

        Used by the two ``OrderService.create_order_from_cart*`` paths so
        each path collapses from a per-provider if/elif tower into one
        registry-dispatched call.
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return
        kind = ShippingKind(order.shipping_kind)
        adapter.create_shipment_row(
            order, kind=kind, payload=payload or {}, items=items
        )

    @classmethod
    def dispatch_create_shipment_task(cls, order: Order) -> None:
        """Fire the provider's create-shipment Celery task.

        The order MUST have ``shipping_provider`` set — orders created
        through either of the ``OrderService.create_order_from_cart*``
        paths always go through ``_resolve_shipping_provider`` which
        sets it. A missing provider here means the order is genuinely
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
        from django.db import connection, transaction

        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return
        # Capture the tenant schema NOW: on_commit fires after the
        # request's schema context can unwind (Stripe replay / manual
        # reprocess), where the carrier task would otherwise dispatch
        # under public and permanently fail on Order.DoesNotExist.
        schema = connection.schema_name
        transaction.on_commit(
            lambda: adapter.dispatch_create_shipment_task(
                order, schema_name=schema
            )
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
        context: Mapping[str, Any] | None = None,
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
        country_code: str,
        order_value_amount: float = 0.0,
        currency: str = "EUR",
        weight_grams: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the matrix of (provider, kind) options for checkout.

        ``country_code`` is required — a per-country ``ShippingRate``
        is what makes a (provider, kind) offerable at all now, so there
        is no destination-agnostic matrix left to return.

        Filters by:
        * ``ShippingProvider.is_active = True``.
        * Provider must have an adapter registered (so deployments
          missing a provider app don't surface its options).
        * The (provider, kind)'s per-kind feature flag
          (``adapter.is_kind_enabled``).
        * An active ``ShippingRate`` exists for
          (provider, country_code, kind) — the single source of
          "does this store ship this way to this country".

        A cart heavier than the rate's ``max_weight_grams`` still gets
        its row, flagged ``exceeds_max_weight`` — the storefront shows
        it disabled with a reason rather than a step that silently has
        one fewer option.
        """
        country_code = country_code.upper()
        order_value = Money(order_value_amount, currency)

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

                rate = (
                    ShippingRate.objects.filter(
                        provider=provider,
                        country_id=country_code,
                        kind=kind.value,
                        is_active=True,
                    )
                    .select_related("provider")
                    .first()
                )
                if rate is None:
                    # No rate for this country — this (provider, kind)
                    # simply does not ship here.
                    continue

                exceeds_max_weight = bool(
                    weight_grams is not None
                    and rate.max_weight_grams is not None
                    and weight_grams > rate.max_weight_grams
                )
                price = cls._priced(
                    rate,
                    country_code=country_code,
                    region_id=None,
                    weight_grams=weight_grams,
                    order_value=order_value,
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
                        "price": price.amount,
                        "currency": str(price.currency),
                        "live_mode": provider.live_mode,
                        "priority": provider.priority,
                        "logo_url": provider.logo_url_for_kind(kind.value),
                        "metadata": provider.metadata or {},
                        "pay_ways": cls._pay_ways_for(
                            provider.code, kind, country_code
                        ),
                        "country_code": country_code,
                        "max_weight_grams": rate.max_weight_grams,
                        "exceeds_max_weight": exceeds_max_weight,
                    }
                )

        options.sort(key=lambda opt: (opt["priority"], opt["provider_code"]))
        return options

    @staticmethod
    def _pay_ways_for(
        provider_code: str,
        kind: ShippingKind,
        country_code: str | None = None,
    ) -> list[dict]:
        """Payment methods this (provider, kind) can actually settle.

        Runs the pay-way rules rather than restating them: the same
        ``PayWayService.filter_by_carrier`` the checkout calls, so the
        delivery step can never advertise a method the payment step
        would then refuse. Duplicating the logic here is how the two
        drift.

        One small query per option (the table holds a handful of rows
        and both layers are indexed). Deliberately not hoisted into a
        single pass: the exclusions are per (provider, kind) and the
        carrier hook is a queryset filter, so there is no shared
        result to reuse — and a hand-rolled bulk version would be a
        second implementation of the rules.

        Failure is contained. This is advertising copy on a delivery
        card; if it raises, the shopper still gets every shipping
        option and the payment step still filters correctly.
        """
        from pay_way.models import PayWay
        from pay_way.services import PayWayService

        try:
            queryset = PayWayService.filter_by_carrier(
                PayWay.objects.filter(active=True),
                provider_code=provider_code,
                shipping_kind=kind.value,
                country_code=country_code,
            )
            return [
                {
                    "id": pay_way.pk,
                    # The KEY, not a label — see the serializer.
                    "name": pay_way.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "",
                }
                for pay_way in queryset.order_by("sort_order", "id")
            ]
        except Exception:
            logger.exception(
                "Could not resolve pay ways for shipping option %s/%s — "
                "the delivery card will advertise none",
                provider_code,
                kind.value,
            )
            return []

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
        """Aggregate per-(active rate) free-shipping thresholds.

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

        ``country_code`` defaults to the first :meth:`shippable_country_
        codes` entry — the same rule checkout uses for its initial
        country — so a caller with no address yet still gets a
        meaningful "from X €" line. The response echoes the country it
        actually used in ``country_code``, which is ``None`` only when
        the store ships nowhere at all yet.

        Filters mirror :meth:`available_options`:
        * ``ShippingProvider.is_active`` and ``ShippingRate.is_active``
          must both be True.
        * The provider's adapter must be registered (so a deploy
          missing a provider app doesn't surface stale rows).
        * The carrier's ``is_kind_enabled(kind)`` hook lets a provider
          gate a kind independently (e.g. ACS Smartpoint hidden
          until ops flip ``ACS_SMARTPOINT_ENABLED``).
        * Rows whose rate has no ``free_shipping_threshold`` are
          skipped — a missing threshold is NOT the same as "free at
          €0".
        """
        active_currency = currency or settings.DEFAULT_CURRENCY

        resolved_country = (country_code or "").upper() or next(
            iter(cls.shippable_country_codes()), None
        )
        if resolved_country is None:
            return {
                "providers": [],
                "min_threshold": None,
                "max_threshold": None,
                "currency": active_currency,
                "country_code": None,
            }

        rates = ShippingRate.objects.filter(
            provider__is_active=True,
            country_id=resolved_country,
            is_active=True,
            free_shipping_threshold__isnull=False,
        ).select_related("provider")

        providers: list[dict[str, Any]] = []
        for rate in rates:
            if not is_registered(rate.provider.code):
                logger.warning(
                    "Active ShippingRate for '%s' has no registered adapter"
                    " — skipping in free_shipping_info()",
                    rate.provider.code,
                )
                continue

            kind_enum = ShippingKind(rate.kind)
            adapter = get_provider(rate.provider.code)
            if not adapter.is_kind_enabled(kind_enum):
                continue

            providers.append(
                {
                    "provider_code": rate.provider.code,
                    "provider_name": rate.provider.name,
                    "kind": rate.kind,
                    "threshold": rate.free_shipping_threshold.amount,
                    "priority": rate.provider.priority,
                }
            )

        providers.sort(key=lambda row: (row["priority"], row["provider_code"]))

        thresholds = [row["threshold"] for row in providers]
        return {
            "providers": providers,
            "min_threshold": min(thresholds) if thresholds else None,
            "max_threshold": max(thresholds) if thresholds else None,
            "currency": active_currency,
            "country_code": resolved_country,
        }

    # ------------------------------------------------------------------
    # Rate resolution + pricing
    # ------------------------------------------------------------------

    @classmethod
    def shippable_country_codes(cls) -> list[str]:
        """Alpha-2 codes with at least one active rate, by ``Country.sort_order``.

        The ordering matches the countries API's own default (``Country``
        is itself ``ordering = ["sort_order"]``), so a caller that lists
        shippable countries and one that lists all countries agree on
        which one is "first" — the rule ``free_shipping_info`` and the
        storefront's initial-country pick both rely on.
        """
        from country.models import Country

        # ``.order_by()`` clears ``ShippingRate.Meta.ordering`` for this
        # queryset — without it, Postgres' ``SELECT DISTINCT`` must
        # include every ``ORDER BY`` column, so a country with rates
        # under two different kinds (different ``ORDER BY`` values)
        # would count as two "distinct" rows instead of one.
        rated_codes = (
            ShippingRate.objects.filter(
                is_active=True, provider__is_active=True
            )
            .order_by()
            .values_list("country_id", flat=True)
            .distinct()
        )
        return list(
            Country.objects.filter(alpha_2__in=rated_codes)
            .order_by("sort_order")
            .values_list("alpha_2", flat=True)
        )

    @classmethod
    def resolve_home_delivery_provider(
        cls, country_code: str | None, weight_grams: int | None = None
    ) -> str | None:
        """Return the active home-delivery carrier's code for ``country_code``.

        Lower ``ShippingProvider.priority`` wins the tie. Without a
        country, falls back to the lowest-priority active home-delivery
        provider regardless of rate coverage (there is nothing more
        specific to prefer yet). Moved from ``OrderService.
        _resolve_active_home_delivery_code`` so both the FK-assignment
        and the pricing paths route ``home_delivery`` through the same
        carrier for the same country — otherwise an order could be
        priced against one carrier's rate and assigned to another.

        With ``weight_grams`` it prefers a carrier whose rate can carry
        that cart, so a capped carrier listed first does not hide an
        uncapped one behind it. When none can, it still names the
        first carrier, so the quote fails as over-weight rather than as
        unavailable for the country.
        """
        qs = ShippingProvider.objects.filter(
            is_active=True, supports_home_delivery=True
        )
        if country_code:
            rate = Q(
                rates__country_id=country_code.upper(),
                rates__kind=ShippingKind.HOME_DELIVERY.value,
                rates__is_active=True,
            )
            if weight_grams is not None:
                # In the same filter() as ``rate``, so both conditions
                # hold on ONE rate row, not on two different ones.
                fitting = qs.filter(
                    rate
                    & (
                        Q(rates__max_weight_grams__isnull=True)
                        | Q(rates__max_weight_grams__gte=weight_grams)
                    )
                )
                picked = fitting.order_by("priority", "code").first()
                if picked is not None:
                    return picked.code
            qs = qs.filter(rate)
        picked = qs.order_by("priority", "code").first()
        return picked.code if picked is not None else None

    @classmethod
    def active_rate(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
    ) -> ShippingRate:
        """Resolve the single active ``ShippingRate`` for this combination.

        The one gate every quote and every order-creation path shares:
        an active provider, a registered adapter, the provider actually
        supporting ``kind``, the kind enabled for checkout, and an
        active rate row for ``country_code``. Raises
        :class:`~shipping.exceptions.ShippingUnavailableError` the
        moment any of those is not true, naming exactly which — a
        missing ``kind`` included, since ``home_delivery`` auto-
        resolution can still leave it unset when the caller never
        supplied one.
        """
        if not provider_code or not kind or not country_code:
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind or "",
                country_code=country_code,
            )
        if not is_registered(provider_code):
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
            )

        country_code = country_code.upper()
        provider = ShippingProvider.objects.filter(
            code=provider_code, is_active=True
        ).first()
        if provider is None or not provider.supports(kind):
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
            )

        adapter = get_provider(provider_code)
        kind_enum = ShippingKind(kind)
        if not adapter.is_kind_enabled(kind_enum):
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
            )

        rate = (
            ShippingRate.objects.select_related("provider")
            .filter(
                provider=provider,
                country_id=country_code,
                kind=kind,
                is_active=True,
            )
            .first()
        )
        if rate is None:
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
            )
        return rate

    @classmethod
    def assert_available(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        weight_grams: int | None = None,
    ) -> ShippingRate:
        """:meth:`active_rate`, plus the weight-cap check.

        Used directly (not through :meth:`quote`) by callers that need
        to gate on availability without needing a priced amount — e.g.
        a free-shipping promotion still must not let an unavailable or
        over-weight option through just because it charges nothing.
        """
        rate = cls.active_rate(
            provider_code=provider_code, kind=kind, country_code=country_code
        )
        if (
            weight_grams is not None
            and rate.max_weight_grams is not None
            and weight_grams > rate.max_weight_grams
        ):
            raise ShippingWeightExceededError(
                weight_grams=weight_grams,
                max_weight_grams=rate.max_weight_grams,
            )
        return rate

    @classmethod
    def _priced(
        cls,
        rate: ShippingRate,
        *,
        country_code: str,
        region_id: str | None,
        weight_grams: int | None,
        order_value: Money,
    ) -> Money:
        """Price ``rate`` for ``order_value`` — threshold, then live quote, then rate price.

        Deliberately does not re-check availability or the weight cap
        (:meth:`assert_available` already did, or the caller is
        display-only and wants a price regardless — see
        ``available_options``, which shows an over-cap option's price
        alongside its ``exceeds_max_weight`` flag rather than hiding it).
        """
        if (
            rate.free_shipping_threshold is not None
            and order_value.amount >= rate.free_shipping_threshold.amount
        ):
            return Money(0, order_value.currency)

        adapter = get_provider(rate.provider.code)
        live = adapter.live_quote(
            rate=rate,
            country_code=country_code,
            region_id=region_id,
            weight_grams=weight_grams,
            currency=str(order_value.currency),
        )
        if live is not None:
            return Money(live, order_value.currency)
        return Money(rate.price.amount, order_value.currency)

    @classmethod
    def quote(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        region_id: str | None = None,
        weight_grams: int | None = None,
        order_value: Money,
    ) -> Money:
        """Gate on availability + weight cap, then price the shipment.

        The single entry point ``OrderService.shipping_cost`` and the
        create-payment-intent view use — replaces the old
        ``calculate_shipping_cost`` dispatcher. Raises
        :class:`~shipping.exceptions.ShippingUnavailableError` /
        :class:`~shipping.exceptions.ShippingWeightExceededError`
        instead of silently falling back to a generic flat rate:
        ``ShippingRate`` is now the only source of a shipping price, so
        "no rate" is a genuine error state, not a gap to paper over.
        """
        rate = cls.assert_available(
            provider_code=provider_code,
            kind=kind,
            country_code=country_code,
            weight_grams=weight_grams,
        )
        price = cls._priced(
            rate,
            country_code=(country_code or "").upper(),
            region_id=region_id,
            weight_grams=weight_grams,
            order_value=order_value,
        )
        # Anchor log for the per-carrier pricing decision. Used by ops
        # to answer "did the free-shipping threshold fire for this
        # cart?" without re-running the carrier adapter under a
        # debugger.
        logger.info(
            "Shipping quote: provider=%s kind=%s country=%s "
            "order_value=%.2f %s weight_grams=%s -> %.2f %s",
            provider_code,
            kind,
            country_code,
            order_value.amount,
            order_value.currency,
            weight_grams,
            price.amount,
            price.currency,
            extra={
                "shipping_provider_code": provider_code,
                "shipping_kind": kind,
                "country_code": country_code,
                "region_id": region_id,
                "order_value_amount": str(order_value.amount),
                "currency": str(order_value.currency),
                "weight_grams": weight_grams,
                "shipping_amount": str(price.amount),
                "is_free": price.amount == 0,
            },
        )
        return price
