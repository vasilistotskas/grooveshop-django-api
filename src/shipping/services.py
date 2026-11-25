"""Top-level shipping dispatcher.

Order-flow code, the Order detail serializer, the payment hook, and
``quote`` go through this module so they never import provider apps
directly. The dispatcher resolves the provider via the DB-backed
``ShippingProvider`` row + the in-memory carrier registry, and the
price/availability/weight-cap via the DB-backed ``ShippingRate`` row.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import reduce
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db.models import Q, QuerySet
from djmoney.money import Money

from core.cache_backend import REDIS_UNAVAILABLE
from shipping.enum import FreeShippingSource, ShippingKind
from shipping.exceptions import (
    PickupPointCheckUnavailableError,
    ShippingParcelTooLargeError,
    ShippingRegionUnavailableError,
    ShippingUnavailableError,
    ShippingWeightExceededError,
)
from shipping.interfaces import (
    PickupPointUnknown,
    PostalAddress,
    ShippingCarrierInterface,
    get_provider,
    is_registered,
    registered_providers,
)
from shipping.models import ShippingProvider, ShippingRate
from shipping.zones import (
    carrier_offers,
    chargeable,
    destination_rates,
    offered,
    reaching,
    standing_destinations,
)

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)

# How long after one checkout resynced a carrier's catalogue for an
# unknown pickup point another miss refuses at once instead of syncing
# again: a full catalogue fetch takes seconds, so a bad or malicious id
# must not be able to start one per request. Per store and carrier (the
# cache key is tenant-scoped). Ten minutes: a point BOX NOW's widget
# shows after the 02:00 sync is found by the first resync, and the next
# chance comes soon enough for a real point added in between.
PICKUP_POINT_RESYNC_GUARD_SECONDS = 600


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


# Between the address line 2 and the customer's note in one notes field.
DELIVERY_NOTES_SEPARATOR = " | "


@dataclass(frozen=True, slots=True)
class CourierNotes:
    """The notes a voucher carries: the floor, the address line 2, then
    the customer's note, within ``DELIVERY_NOTES_MAX_LEN``.

    For a courier whose recipient address field is the street alone (ACS's
    ``Recipient_Address``: "the street without the area or place"), the
    flat or floor rides in the free-text notes. The floor and the address
    line are never cut (both are short: a label, and a field capped well
    under the limit); the customer's note is cut first to make room. Each
    part is sanitised as :func:`sanitize_delivery_notes` does.
    """

    address_line_2: str
    customer_notes: str
    floor: str = ""

    def _parts(self) -> tuple[str, str, str]:
        """The floor, the address line and the customer's note, each as
        :attr:`sent` carries it."""
        floor = sanitize_delivery_notes(self.floor)
        line = sanitize_delivery_notes(self.address_line_2)
        notes = sanitize_delivery_notes(self.customer_notes)
        head = DELIVERY_NOTES_SEPARATOR.join(p for p in (floor, line) if p)
        if head and notes:
            room = (
                DELIVERY_NOTES_MAX_LEN
                - len(head)
                - len(DELIVERY_NOTES_SEPARATOR)
            )
            notes = notes[:room].rstrip() if room > 0 else ""
        return floor, line, notes

    @property
    def sent(self) -> str:
        """The text the courier receives."""
        return DELIVERY_NOTES_SEPARATOR.join(p for p in self._parts() if p)

    @property
    def customer_part(self) -> str:
        """The customer's note as :attr:`sent` carries it: cut to make room
        for the floor and the address line, not cut again on its own."""
        return self._parts()[2]


@dataclass(frozen=True, slots=True)
class ShippingCharge:
    """What a shipping option costs a cart, and what made it free.

    ``waived_by`` is set only when a rule took a real price to 0: the
    rate's own free-shipping threshold, or a free-shipping promotion. A
    rate that simply costs nothing has none.
    """

    price: Money
    waived_by: FreeShippingSource | None = None


@dataclass(frozen=True, slots=True)
class ProductShippingEstimate:
    """What shipping one product on its own costs, at the cheapest.

    ``price`` is the cheapest delivery checkout would charge a cart
    holding that product alone, to ``country_code``; ``free_shipping_
    threshold`` the lowest products subtotal from which a carrier that
    can take it ships free, ``None`` when none ever does.
    """

    country_code: str
    price: Money
    free_shipping_threshold: Decimal | None

    @property
    def free(self) -> bool:
        return self.price.amount == 0


class ShippingService:
    """Provider-agnostic dispatcher used by the rest of the codebase."""

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def pickup_point_address(
        cls,
        provider_code: str,
        location: Mapping[str, str],
        *,
        country_code: str,
    ) -> PostalAddress:
        """The address of the pickup point *location* names, from the
        carrier's catalogue, resyncing it once when the point is missing.

        The storefront picks a BOX NOW locker in BOX NOW's own widget
        (live data) while the catalogue syncs daily, so a new locker can
        be missing for up to a day; an ACS station can be missing through
        the gateway or a stale cache. On a miss the catalogue is fetched
        again through the scheduled sync's own path
        (``refresh_pickup_points``), before any transaction or lock, and
        read once more. The point must be deliverable
        (``deliverable_pickup_point``: held, active, in the order's
        country); a withdrawn one counts as missing. No resync runs where
        the store does not deliver to that kind in that country
        (``active_rate``), and at most one per store and carrier in
        ``PICKUP_POINT_RESYNC_GUARD_SECONDS``: inside it a miss is refused
        at once. A resync that fails or passes its deadline
        (``_resync_within_deadline``) is logged and refused. Raises
        ``PickupPointUnknown`` or ``PickupPointInAnotherCountry``, or
        ``PickupPointCheckUnavailableError`` when the guard's cache is down
        (no resync, nothing judged); never answers with any other address.
        """
        from django.core.cache import cache

        carrier = get_provider(provider_code)
        try:
            return carrier.deliverable_pickup_point(
                location, country_code=country_code
            )
        except PickupPointUnknown:
            pass
        # A store that cannot deliver to that kind in that country refuses
        # anyway: no carrier is asked for it.
        try:
            cls.active_rate(
                provider_code=provider_code,
                kind=ShippingKind.PICKUP_POINT,
                country_code=country_code,
            )
        except ShippingUnavailableError as e:
            raise PickupPointUnknown(
                f"{provider_code} pickup points are not offered in "
                f"{country_code}"
            ) from e
        guard = f"shipping:pickup-point-resync:{provider_code}"
        try:
            acquired = cache.add(
                guard, 1, timeout=PICKUP_POINT_RESYNC_GUARD_SECONDS
            )
        except REDIS_UNAVAILABLE as e:
            # ``CustomCache.add`` raises while Redis is down: without the
            # guard nothing bounds the resyncs, so none runs.
            logger.warning(
                "No resync of the %s catalogue for pickup point %s: the "
                "cache holding its guard is unavailable",
                provider_code,
                dict(location),
                exc_info=True,
            )
            raise PickupPointCheckUnavailableError(
                f"{provider_code} pickup point {dict(location)} is unknown "
                "and its catalogue cannot be resynced without the cache"
            ) from e
        if not acquired:
            raise PickupPointUnknown(
                f"{provider_code} pickup point {dict(location)} is unknown "
                "and its catalogue was resynced in the last "
                f"{PICKUP_POINT_RESYNC_GUARD_SECONDS} s"
            )
        cls._resync_within_deadline(
            carrier, provider_code, location, country_code=country_code
        )
        return carrier.deliverable_pickup_point(
            location, country_code=country_code
        )

    @staticmethod
    def _resync_within_deadline(
        carrier: ShippingCarrierInterface,
        provider_code: str,
        location: Mapping[str, str],
        *,
        country_code: str,
    ) -> None:
        """Run the carrier's resync, waiting at most
        ``PICKUP_POINT_RESYNC_TIMEOUT_SECONDS`` for it.

        The sync runs in a daemon thread of its own, on the request's
        store schema (the pattern of ``agent.outbox``'s event call),
        because a carrier client has no deadline for a whole sync
        (each call retries within its own timeout, so a sync can take
        minutes) and the order call has callers that wait seconds. Past
        the deadline the order is refused; the thread finishes the sync,
        so the next order finds the point. A failure is logged and
        refused alike.
        """
        import threading

        from django.db import connection
        from django_tenants.utils import schema_context

        schema = connection.schema_name
        failure: list[BaseException] = []

        def resync() -> None:
            # A thread of its own outlives a request that stops waiting:
            # it sets its store's schema, logs what goes wrong (nobody may
            # read its result), and closes the connection it opened.
            try:
                with schema_context(schema):
                    carrier.refresh_pickup_points(country_code=country_code)
            except Exception as error:
                logger.exception(
                    "Resync of the %s catalogue for pickup point %s failed",
                    provider_code,
                    dict(location),
                )
                failure.append(error)
            finally:
                connection.close()

        # The guard (``pickup_point_address``) is taken before this thread
        # starts, so requests that stop waiting never run two syncs.
        worker = threading.Thread(
            target=resync, name="pickup-point-resync", daemon=True
        )
        worker.start()
        worker.join(settings.PICKUP_POINT_RESYNC_TIMEOUT_SECONDS)
        if worker.is_alive():
            logger.warning(
                "Resync of the %s catalogue for pickup point %s did not "
                "finish within %s s; the order is refused and the sync "
                "goes on",
                provider_code,
                dict(location),
                settings.PICKUP_POINT_RESYNC_TIMEOUT_SECONDS,
            )
            raise PickupPointUnknown(
                f"{provider_code} catalogue resync passed its deadline"
            )
        if failure:
            raise PickupPointUnknown(
                f"{provider_code} catalogue could not be resynced"
            ) from failure[0]

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
        paths always go through ``_assign_shipping_provider``, which
        sets it from the quote. A missing provider here means the order is genuinely
        provider-less (e.g. flat-rate home delivery without a courier
        adapter) — silently return.

        Defers the per-carrier dispatch with ``transaction.on_commit``
        so the Celery worker never receives the order_id before the
        creating transaction has committed. Without this guard the
        worker observed a "Order N not found" race (verified on prod
        order 47, 2026-04-30): the task fired inside the open
        ``create_order_from_cart`` transaction, the worker
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
    def can_cancel_shipment(cls, order: Order) -> bool:
        """Whether the order's carrier would still accept a cancel.

        Local data only, no carrier call; True when the order has no
        shipment. See :meth:`ShippingCarrierInterface.can_cancel_shipment`.
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return True
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return True
        return adapter.can_cancel_shipment(shipment)

    @classmethod
    def canceled_shipment_q(cls) -> Q | None:
        """Orders a carrier left open behind a canceled shipment, as a
        ``Q`` on ``Order``: every carrier's answer
        (:meth:`ShippingCarrierInterface.canceled_shipment_q`) OR-ed, or
        ``None`` when no carrier leaves one."""
        rules = [
            rule
            for adapter in registered_providers()
            if (rule := adapter.canceled_shipment_q()) is not None
        ]
        return reduce(operator.or_, rules) if rules else None

    @classmethod
    def moves_by_carrier(cls, order: Order) -> bool:
        """Whether a carrier takes the order's parcel: its provider keeps a
        shipment of its own (a voucher, a locker parcel). The merchant's
        own ``flat_rate`` delivery keeps none."""
        adapter = cls.adapter_for_order(order)
        return (
            adapter is not None
            and adapter.shipment_for_order(order) is not None
        )

    @classmethod
    def carrier_holds_the_parcel(cls, order: Order) -> bool:
        """Whether the order's carrier has accepted its parcel (a voucher or
        parcel at the carrier, ``holds_the_parcel``). A tracking number
        typed on the order, or a shipment row written at checkout, is not
        that."""
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return False
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return False
        # As stored: the order may hold the row cached from before the
        # carrier path saved the voucher or parcel on its own (locked)
        # copy and then recorded the tracking number on the order.
        return adapter.holds_the_parcel(
            type(shipment)._default_manager.get(pk=shipment.pk)
        )

    @classmethod
    def delivery_address(cls, order: Order) -> PostalAddress | None:
        """Where the order's carrier takes the parcel, or ``None`` when the
        order does not move through a carrier (no provider, or one with no
        shipment of its own, like the merchant's ``flat_rate`` delivery).

        The pickup point's address for a locker or station order
        (:meth:`ShippingCarrierInterface.pickup_point_address`), the
        buyer's shipping address otherwise. Raises ``ValueError`` when the
        pickup point's address is not known.
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        shipment = adapter.shipment_for_order(order)
        if shipment is None:
            return None
        return adapter.pickup_point_address(shipment) or PostalAddress(
            street=order.street,
            street_number=order.street_number,
            postal_code=order.zipcode,
            city=order.city,
        )

    @classmethod
    def fetch_label_bytes(cls, order: Order) -> bytes | None:
        """Return the label PDF for ``order``, or None when it has none.

        None covers every "no label" case: no carrier, no shipment row,
        or a shipment the carrier has not booked yet
        (:meth:`ShippingCarrierInterface.has_label`). The carrier is
        called only for a label that exists.
        """
        adapter = cls.adapter_for_order(order)
        if adapter is None:
            return None
        shipment = adapter.shipment_for_order(order)
        if shipment is None or not adapter.has_label(shipment):
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
        order_value_amount: Decimal | float = 0.0,
        currency: str = "EUR",
        weight_grams: int | None = None,
        items: Iterable[tuple[Any, int]] | None = None,
        region_id: str | None = None,
        free: bool = False,
    ) -> list[dict[str, Any]]:
        """Return the matrix of (provider, kind) options for checkout.

        ``country_code`` is required — a ``ShippingRate`` of a zone of
        the country is what makes a (provider, kind) offerable at all,
        so there is no destination-agnostic matrix to return.

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
        one fewer option. The same goes for ``items`` (the cart's
        ``(product, quantity)`` pairs, when the caller's cart is known)
        that do not fit the carrier's parcels: ``exceeds_parcel_size``,
        decided by the rule :meth:`assert_available` enforces.

        ``region_id`` picks the zone a home delivery is priced by
        (``shipping.zones``): without one, the country's base zone, the
        rows a checkout shows before the shopper picks a region. It and
        ``free`` (a free-shipping promotion) are what :meth:`quote` prices
        with, so a caller that knows them (the checkout quote) gets each
        row at the price an order would be charged, with ``waived_by``
        saying what made it free.
        """
        items = None if items is None else list(items)
        country_code = country_code.upper()
        order_value = Money(order_value_amount, currency)

        options: list[dict[str, Any]] = []
        for provider, kind, adapter, rate in cls.offered_rates(
            country_code, region_id
        ):
            exceeds_max_weight = bool(
                weight_grams is not None
                and rate.max_weight_grams is not None
                and weight_grams > rate.max_weight_grams
            )
            exceeds_parcel_size = False
            compartment_size = None
            if items is not None:
                try:
                    adapter.assert_parcel_fits(kind=kind, items=items)
                except ShippingParcelTooLargeError:
                    exceeds_parcel_size = True
                else:
                    compartment_size = adapter.parcel_size(
                        kind=kind, items=items
                    )
            charge = cls._priced(
                rate,
                country_code=country_code,
                region_id=region_id,
                weight_grams=weight_grams,
                order_value=order_value,
                free=free,
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
                    "price": charge.price.amount,
                    "waived_by": charge.waived_by,
                    "currency": str(charge.price.currency),
                    "live_mode": provider.live_mode,
                    "priority": provider.priority,
                    "logo_path": provider.logo_path_for_kind(kind.value),
                    "metadata": provider.metadata or {},
                    "pay_ways": cls._pay_ways_for(
                        provider.code, kind, country_code
                    ),
                    "country_code": country_code,
                    "max_weight_grams": rate.max_weight_grams,
                    "exceeds_max_weight": exceeds_max_weight,
                    "exceeds_parcel_size": exceeds_parcel_size,
                    "compartment_size": compartment_size,
                    "delivery_days_min": rate.delivery_days_min,
                    "delivery_days_max": rate.delivery_days_max,
                }
            )

        options.sort(key=lambda opt: (opt["priority"], opt["provider_code"]))
        return options

    @classmethod
    def product_shipping_from(cls, product) -> ProductShippingEstimate | None:
        """The cheapest delivery of ``product`` bought on its own.

        Priced as checkout prices a cart holding one unit of it and
        nothing else (:meth:`available_options`), for a shopper not known
        yet: to the store's first shipping country, the one checkout
        preselects (:meth:`initial_country_code`), at its standing
        destinations (``shipping.zones.standing_destinations``: the base
        zone for what it offers, each region zone for a home delivery it
        does not, the cheapest winning); at the products value
        every "free above X" rule is judged on, after the automatic
        promotions such a cart gets
        (``PromotionEngine.evaluate_single_unit``), whose free shipping
        waives every carrier; weighed and sized with the gifts those
        promotions add, so a delivery that cannot carry the parcel
        (``exceeds_max_weight`` / ``exceeds_parcel_size``) does not
        count. A carrier priced live is asked as checkout asks it.

        ``None`` when the store ships nowhere, or no delivery it offers
        to that country can take the product.
        """
        from order.checkout.quote import OrderQuoteService
        from promotion.engine.benefits import gift_pairs
        from promotion.engine.evaluation import PromotionEngine
        from shipping.utils import compute_total_weight_grams

        country_code = cls.initial_country_code()
        if country_code is None:
            return None

        promotion = PromotionEngine.evaluate_single_unit(product)
        items = [(product, 1), *gift_pairs(promotion)]
        weight_grams = compute_total_weight_grams(items)
        order_value = OrderQuoteService.threshold_value(
            product.final_price, promotion.discount_total
        )
        prices = [
            option["price"]
            for destination in standing_destinations([country_code])[
                country_code
            ]
            for option in cls.available_options(
                country_code=country_code,
                region_id=destination.region_code,
                order_value_amount=order_value.amount,
                currency=str(order_value.currency),
                weight_grams=weight_grams,
                items=items,
                free=promotion.free_shipping,
            )
            if option["kind"] in destination.kinds
            and not option["exceeds_max_weight"]
            and not option["exceeds_parcel_size"]
        ]
        if not prices:
            return None

        free_rates = cls.free_threshold_rates(
            country_code, weight_grams=weight_grams, items=items
        )
        return ProductShippingEstimate(
            country_code=country_code,
            price=Money(min(prices), order_value.currency),
            free_shipping_threshold=(
                free_rates[0].free_shipping_threshold.amount
                if free_rates
                else None
            ),
        )

    @classmethod
    def offered_rates(
        cls, country_code: str, region_id: str | None = None
    ) -> Iterator[
        tuple[
            ShippingProvider,
            ShippingKind,
            ShippingCarrierInterface,
            ShippingRate,
        ]
    ]:
        """Every (provider, kind) this store ships to ``country_code``
        (and ``region_id``, for a home delivery).

        The one definition of "the store offers this", shared by
        :meth:`available_options` (checkout) and :meth:`shipping_policy`
        (the public policy), so the two can never disagree. A
        combination is offered when:

        * its ``ShippingProvider`` is active and supports the kind;
        * the provider has an adapter registered (so deployments
          missing a provider app don't surface its options);
        * the adapter's per-kind gate (``is_kind_enabled``) lets it
          through — a provider can advertise capability via the
          ``ShippingProvider`` row but gate user-facing visibility on
          state it controls itself (ACS lockers need synced stations);
        * an active ``ShippingRate`` exists for (provider, kind) in the
          zone the destination resolves to (``shipping.zones.
          destination_rates``) — the single source of "does this store
          ship this way there".
        """
        rates = {
            (rate.provider_id, rate.kind): rate
            for rate in destination_rates(country_code, region_id)
            .filter(is_active=True)
            .select_related("provider")
        }
        qs = (
            ShippingProvider.objects.filter(is_active=True)
            .filter(
                Q(supports_home_delivery=True) | Q(supports_pickup_point=True)
            )
            .order_by("priority", "code")
        )

        for provider in qs:
            if not is_registered(provider.code):
                logger.warning(
                    "Active ShippingProvider '%s' has no registered adapter "
                    "— skipping in offered_rates()",
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
                if not adapter.is_kind_enabled(kind, country_code=country_code):
                    continue

                rate = rates.get((provider.pk, kind.value))
                if rate is None:
                    # No rate for this destination — this (provider,
                    # kind) simply does not ship here.
                    continue

                yield provider, kind, adapter, rate

    @classmethod
    def shipping_policy(cls) -> list[dict[str, Any]]:
        """The store's standing shipping policy, one row per offer.

        One row per (provider, kind, country) the store actually offers
        (:meth:`offered_rates`), over :meth:`shippable_country_codes`
        and each country's standing destinations
        (``shipping.zones.standing_destinations``, read for every country
        at once): the base zone, whose rows carry no ``region_codes``, and,
        for a home delivery it does not offer, each region zone that does,
        whose rows name its regions;
        straight from the ``ShippingRate``: no cart, weight or order
        value is involved, so the price is the rate's own, before a
        live carrier quote or a free-shipping threshold. Powers
        ``GET /api/v1/shipping/policy`` which the storefront turns into
        its Organization ``hasShippingService`` markup.
        """
        countries = cls.shippable_country_codes()
        standing = standing_destinations(countries)
        return [
            {
                "country_code": country_code,
                "kind": kind.value,
                "provider_code": provider.code,
                "provider_name": provider.name,
                "price": rate.price.amount,
                "currency": str(rate.price.currency),
                "free_shipping_threshold": (
                    None
                    if rate.free_shipping_threshold is None
                    else rate.free_shipping_threshold.amount
                ),
                "delivery_days_min": rate.delivery_days_min,
                "delivery_days_max": rate.delivery_days_max,
                "max_weight_grams": rate.max_weight_grams,
                "region_codes": list(destination.region_codes),
            }
            for country_code in countries
            for destination in standing[country_code]
            for provider, kind, _adapter, rate in cls.offered_rates(
                country_code, destination.region_code
            )
            if kind.value in destination.kinds
        ]

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
                    "key": pay_way.key,
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

        Powers ``GET /api/v1/shipping/free-shipping-info``, the store's
        standing "Δωρεάν μεταφορικά άνω των X €" promise, which the
        storefront shows where there is no cart to judge (a product
        page, a visitor who has not added anything). How far a CART is
        from free shipping is the cart's own ``free_shipping_progress``
        (``OrderQuoteService.free_shipping_progress``), never this: it also
        weighs the free-shipping promotions and what the cart weighs.
        The semantics keep marketing honest:

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

        The rows are :meth:`free_threshold_rates`, the rates checkout
        could actually price for free.
        """
        active_currency = currency or settings.DEFAULT_CURRENCY

        resolved_country = (
            country_code or ""
        ).upper() or cls.initial_country_code()
        if resolved_country is None:
            return {
                "providers": [],
                "min_threshold": None,
                "max_threshold": None,
                "currency": active_currency,
                "country_code": None,
            }

        providers: list[dict[str, Any]] = [
            {
                "provider_code": rate.provider.code,
                "provider_name": rate.provider.name,
                "kind": rate.kind,
                "threshold": rate.free_shipping_threshold.amount,
                "priority": rate.provider.priority,
            }
            for rate in cls.free_threshold_rates(resolved_country)
        ]

        providers.sort(key=lambda row: (row["priority"], row["provider_code"]))

        thresholds = [row["threshold"] for row in providers]
        return {
            "providers": providers,
            "min_threshold": min(thresholds) if thresholds else None,
            "max_threshold": max(thresholds) if thresholds else None,
            "currency": active_currency,
            "country_code": resolved_country,
        }

    @classmethod
    def free_threshold_rates(
        cls,
        country_code: str,
        *,
        weight_grams: int | None = None,
        items: Iterable[tuple[Any, int]] | None = None,
    ) -> list[ShippingRate]:
        """The rates to ``country_code`` that ship free above a threshold.

        Judged for the country with no region, as every caller here
        knows none: at its standing destinations
        (``shipping.zones.standing_destinations``), the base zone for
        what it offers, each region zone for a home delivery it does not.

        Each passes the gate :meth:`active_rate` puts on a quote (an
        active provider with a registered adapter, supporting the kind,
        the kind enabled for the country, an active rate), and has a
        ``free_shipping_threshold``: a missing threshold is NOT "free at
        €0". With ``weight_grams`` / ``items`` (a cart's ``(product,
        quantity)`` pairs) a rate must also carry that cart, by the
        weight cap and parcel rule :meth:`assert_available` enforces: a
        carrier that cannot take the parcel cannot ship it free either.
        Sorted by threshold, lowest first.
        """
        country_code = country_code.upper()
        items = None if items is None else list(items)
        standing = reduce(
            operator.or_,
            (
                destination_rates(country_code, destination.region_code).filter(
                    kind__in=destination.kinds
                )
                for destination in standing_destinations([country_code])[
                    country_code
                ]
            ),
            ShippingRate.objects.none(),
        )
        rates = (
            chargeable(standing)
            .filter(free_shipping_threshold__isnull=False)
            .select_related("provider")
        )

        usable: list[ShippingRate] = []
        for rate in rates:
            if not is_registered(rate.provider.code):
                logger.warning(
                    "Active ShippingRate for '%s' has no registered adapter"
                    " — skipping in free_threshold_rates()",
                    rate.provider.code,
                )
                continue
            if not rate.provider.supports(rate.kind):
                continue
            kind = ShippingKind(rate.kind)
            adapter = get_provider(rate.provider.code)
            if not adapter.is_kind_enabled(kind, country_code=country_code):
                continue
            if (
                weight_grams is not None
                and rate.max_weight_grams is not None
                and weight_grams > rate.max_weight_grams
            ):
                continue
            if items is not None:
                try:
                    adapter.assert_parcel_fits(kind=kind, items=items)
                except ShippingParcelTooLargeError:
                    continue
            usable.append(rate)

        usable.sort(key=lambda rate: rate.free_shipping_threshold.amount)
        return usable

    # ------------------------------------------------------------------
    # Rate resolution + pricing
    # ------------------------------------------------------------------

    @classmethod
    def initial_country_code(cls) -> str | None:
        """The country a caller with no address yet is judged for.

        The first :meth:`shippable_country_codes` entry, the same one
        the storefront's checkout preselects; ``None`` when the store
        ships nowhere yet.
        """
        return next(iter(cls.shippable_country_codes()), None)

    @classmethod
    def shippable_country_codes(cls) -> list[str]:
        """Alpha-2 codes with at least one active rate, by ``Country.sort_order``.

        A rate in any of a country's zones counts, a region zone's too:
        the store ships somewhere in that country. Its carrier must pass
        the gate a quote puts on it (``shipping.zones.offered``): ACS
        without the store's credentials, or its lockers in a country with
        no stations, makes no country shippable. That is one query for
        the rates and one adapter answer per (carrier, kind).

        The ordering matches the countries API's own default (``Country``
        is itself ``ordering = ["sort_order"]``), so a caller that lists
        shippable countries and one that lists all countries agree on
        which one is "first" — the rule ``free_shipping_info`` and the
        storefront's initial-country pick both rely on.
        """
        from country.models import Country

        rated_codes = {
            rate.zone.country_id for rate in offered(ShippingRate.objects.all())
        }
        return list(
            Country.objects.filter(alpha_2__in=rated_codes)
            .order_by("sort_order")
            .values_list("alpha_2", flat=True)
        )

    @classmethod
    def resolve_home_delivery_provider(
        cls,
        country_code: str | None,
        weight_grams: int | None = None,
        region_id: str | None = None,
    ) -> str | None:
        """Return the active home-delivery carrier's code for ``country_code``.

        Only a carrier the quote's gate takes there is a candidate
        (:func:`~shipping.zones.carrier_offers`). Lower
        ``ShippingProvider.priority`` wins the tie. Without a
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

        With ``region_id`` the carrier is one whose rate is in the zone
        the region resolves to (``shipping.zones``). When none is, while
        one delivers home elsewhere in the country, it names that one, so
        the quote fails as not reaching the region
        (``ShippingRegionUnavailableError``) rather than as unavailable
        for the country.
        """
        carriers = ShippingProvider.objects.filter(
            is_active=True, supports_home_delivery=True
        )
        if not country_code:
            picked = carriers.order_by("priority", "code").first()
            return picked.code if picked is not None else None
        country_code = country_code.upper()
        # Only a carrier the quote's gate takes in the country
        # (``carrier_offers``, as in :meth:`active_rate`): one ahead in
        # priority that checkout refuses (ACS without the store's
        # credentials) would otherwise be named, and the quote fail.
        carriers = carriers.filter(
            pk__in=[
                carrier.pk
                for carrier in carriers
                if carrier_offers(
                    carrier, ShippingKind.HOME_DELIVERY, country_code
                )
            ]
        )
        code = cls._home_delivery_carrier(
            carriers, destination_rates(country_code, region_id), weight_grams
        )
        if code is None and region_id:
            code = cls._home_delivery_carrier(
                carriers,
                reaching(
                    ShippingRate.objects.filter(zone__country_id=country_code)
                ),
                weight_grams,
            )
        return code

    @staticmethod
    def _home_delivery_carrier(
        carriers: QuerySet[ShippingProvider],
        rates: QuerySet[ShippingRate],
        weight_grams: int | None,
    ) -> str | None:
        """The first of ``carriers`` with an active home-delivery rate
        among ``rates``, preferring one whose rate carries
        ``weight_grams`` (:meth:`resolve_home_delivery_provider`)."""
        rate = Q(
            rates__in=rates,
            rates__kind=ShippingKind.HOME_DELIVERY.value,
            rates__is_active=True,
        )
        if weight_grams is not None:
            # In the same filter() as ``rate``, so both conditions hold
            # on ONE rate row, not on two different ones.
            fitting = carriers.filter(
                rate
                & (
                    Q(rates__max_weight_grams__isnull=True)
                    | Q(rates__max_weight_grams__gte=weight_grams)
                )
            )
            picked = fitting.order_by("priority", "code").first()
            if picked is not None:
                return picked.code
        picked = carriers.filter(rate).order_by("priority", "code").first()
        return picked.code if picked is not None else None

    @classmethod
    def active_rate(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        region_id: str | None = None,
    ) -> ShippingRate:
        """Resolve the single active ``ShippingRate`` for this combination.

        The one gate every quote and every order-creation path shares:
        an active provider, a registered adapter, the provider actually
        supporting ``kind``, the kind enabled for checkout, and an
        active rate in the zone ``(country_code, region_id)`` resolves
        to (``shipping.zones``). A carrier that has the rate elsewhere in
        the country but not in that zone raises
        :class:`~shipping.exceptions.ShippingRegionUnavailableError`.
        Raises
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
        country_code = country_code.upper()
        provider = ShippingProvider.objects.filter(
            code=provider_code, is_active=True
        ).first()
        if provider is None or not carrier_offers(provider, kind, country_code):
            raise ShippingUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
            )

        rate = (
            destination_rates(country_code, region_id)
            .select_related("provider")
            .filter(provider=provider, kind=kind, is_active=True)
            .first()
        )
        if rate is not None:
            return rate
        if (
            region_id
            and reaching(
                ShippingRate.objects.filter(
                    provider=provider,
                    kind=kind,
                    zone__country_id=country_code,
                    is_active=True,
                )
            ).exists()
        ):
            # The carrier delivers this way in the country, only not to
            # the region: its zone has no such rate.
            raise ShippingRegionUnavailableError(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
                region_code=region_id,
            )
        raise ShippingUnavailableError(
            provider_code=provider_code,
            kind=kind,
            country_code=country_code,
        )

    @staticmethod
    def assert_pickup_point_fits(
        provider_code: str,
        location: Mapping[str, str],
        *,
        items: Iterable[tuple[Any, int]],
    ) -> None:
        """Raise ``PickupPointUnfit`` when the carrier's pickup point
        *location* names cannot take ``items``
        (``ShippingCarrierInterface.assert_pickup_point_fits``)."""
        get_provider(provider_code).assert_pickup_point_fits(
            location, items=items
        )

    @classmethod
    def assert_available(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        region_id: str | None = None,
        weight_grams: int | None = None,
        items: Iterable[tuple[Any, int]] | None = None,
    ) -> ShippingRate:
        """:meth:`active_rate`, plus the weight-cap and parcel-size checks.

        ``items`` are the cart's ``(product, quantity)`` pairs; the
        carrier adapter decides whether they fit its parcels
        (``ShippingCarrierInterface.assert_parcel_fits``). ``None``
        skips that check.

        Used directly (not through :meth:`quote`) by callers that need
        to gate on availability without needing a priced amount — e.g.
        a free-shipping promotion still must not let an unavailable or
        over-weight option through just because it charges nothing.
        """
        rate = cls.active_rate(
            provider_code=provider_code,
            kind=kind,
            country_code=country_code,
            region_id=region_id,
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
        if items is not None:
            get_provider(rate.provider.code).assert_parcel_fits(
                kind=ShippingKind(rate.kind), items=items
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
        free: bool = False,
        cached_only: bool = False,
    ) -> ShippingCharge:
        """Price ``rate`` for ``order_value`` — promotion, then threshold, then live quote, then rate price.

        ``free`` is a free-shipping promotion: it waives every rate.
        ``waived_by`` is set only for a rate that has a price to waive: a
        delivery that costs nothing anyway was not made free by anything.

        ``cached_only`` keeps the carrier from being asked (see
        ``ShippingCarrierInterface.live_quote``).

        Deliberately does not re-check availability or the weight cap
        (:meth:`assert_available` already did, or the caller is
        display-only and wants a price regardless — see
        ``available_options``, which shows an over-cap option's price
        alongside its ``exceeds_max_weight`` flag rather than hiding it).
        """
        waiver = None
        if free:
            waiver = FreeShippingSource.PROMOTION
        elif (
            rate.free_shipping_threshold is not None
            and order_value.amount >= rate.free_shipping_threshold.amount
        ):
            waiver = FreeShippingSource.THRESHOLD
        if waiver is not None:
            return ShippingCharge(
                Money(0, order_value.currency),
                waiver if rate.price.amount > 0 else None,
            )

        adapter = get_provider(rate.provider.code)
        live = adapter.live_quote(
            rate=rate,
            country_code=country_code,
            region_id=region_id,
            weight_grams=weight_grams,
            currency=str(order_value.currency),
            cached_only=cached_only,
        )
        if live is not None:
            return ShippingCharge(Money(live, order_value.currency))
        return ShippingCharge(Money(rate.price.amount, order_value.currency))

    @classmethod
    def resolve_live_price(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        region_id: str | None = None,
        weight_grams: int | None = None,
    ) -> None:
        """Have the carrier's live price for this delivery resolved and
        cached, so a later ``quote(cached_only=True)`` finds it.

        Order creation calls it before it takes its locks: a carrier call
        can take seconds, and nothing may wait on a carrier while rows
        are locked. Whatever the answer (a price, or none: the stored
        rate is charged), the carrier's adapter remembers it. A delivery
        the store does not offer has nothing to resolve; the quote
        refuses it.
        """
        try:
            rate = cls.active_rate(
                provider_code=provider_code,
                kind=kind,
                country_code=country_code,
                region_id=region_id,
            )
        except ShippingUnavailableError:
            return
        get_provider(rate.provider.code).live_quote(
            rate=rate,
            country_code=(country_code or "").upper(),
            region_id=region_id,
            weight_grams=weight_grams,
            currency=str(rate.price.currency),
        )

    @classmethod
    def quote(
        cls,
        *,
        provider_code: str | None,
        kind: str | None,
        country_code: str | None,
        region_id: str | None = None,
        weight_grams: int | None = None,
        items: Iterable[tuple[Any, int]] | None = None,
        order_value: Money,
        free: bool = False,
        cached_only: bool = False,
    ) -> ShippingCharge:
        """Gate on availability, weight cap and parcel size, then price.

        ``cached_only`` is for a caller holding row locks: no carrier is
        asked (``ShippingLivePriceUnresolvedError`` when a live price is
        needed and was not resolved first, :meth:`resolve_live_price`).

        The single entry point ``OrderQuoteService.shipping_cost`` uses —
        replaces the old ``calculate_shipping_cost`` dispatcher.
        ``free`` is a free-shipping promotion: the price is 0, but the
        gate still runs, so a promotion never makes an unavailable or
        over-weight option orderable. Raises
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
            region_id=region_id,
            weight_grams=weight_grams,
            items=items,
        )
        charge = cls._priced(
            rate,
            country_code=(country_code or "").upper(),
            region_id=region_id,
            weight_grams=weight_grams,
            order_value=order_value,
            free=free,
            cached_only=cached_only,
        )
        price = charge.price
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
        return charge
