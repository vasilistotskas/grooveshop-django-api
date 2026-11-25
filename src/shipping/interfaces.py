"""Carrier-adapter interface and in-memory registry.

A "carrier" is a courier integration (BoxNow, ACS, ELTA, ...).  Each
provider app implements this interface once and registers its adapter
class in ``AppConfig.ready()``:

    @register_provider
    class AcsCarrier(ShippingCarrierInterface):
        code = "acs"
        ...

Higher layers (``ShippingService``, the order-flow code, the
``OrderDetailSerializer``) never import provider apps directly — they
look up the adapter via :func:`get_provider`.  This is what makes the
abstraction *dynamic*: adding a new provider is one new app + one
``ShippingProvider`` row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from django.dispatch import Signal
from django.utils.translation import gettext_lazy as _

from shipping.exceptions import ShippingProviderNotFoundError

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from django.contrib.admin.options import InlineModelAdmin
    from django.db.models import Q
    from django_stubs_ext import StrOrPromise
    from djmoney.money import Money
    from rest_framework.serializers import BaseSerializer, Field

    from order.models.order import Order
    from pay_way.enum.settlement import PaySettlement
    from shipping.demo import ShipmentRow
    from shipping.enum import ShippingKind
    from shipping.models import ShippingRate


@dataclass(frozen=True)
class PostalAddress:
    """A place goods are loaded at or delivered to, as AADE's
    ``AddressType`` holds one: the street and its number are optional,
    the postcode and the city are not."""

    street: str
    street_number: str
    postal_code: str
    city: str


@dataclass(frozen=True)
class AdminPage:
    """One admin page a carrier adds to the sidebar: the model whose
    changelist it opens (``app_label.Model``), its title and its icon."""

    model: str
    title: StrOrPromise
    icon: str = ""
    # Where it sits among the same section's pages of every carrier:
    # lower first, carriers that tie in the order they registered.
    rank: int = 0


@dataclass(frozen=True)
class AdminPages:
    """The admin pages of a carrier, by the sidebar section they sit in:
    the store's Shipping group (``shipments``), its Pickup Points menu, and
    the platform's Audit & Logs and Reconciliation menus. A store that does
    not use the carrier keeps none of the first two; the platform's System
    section keeps the logs."""

    shipments: tuple[AdminPage, ...] = ()
    pickup_points: tuple[AdminPage, ...] = ()
    audit: tuple[AdminPage, ...] = ()
    reconciliation: tuple[AdminPage, ...] = ()


@dataclass(frozen=True)
class AttentionRow:
    """One line of the dashboard's "Needs attention" list for work a
    carrier leaves waiting: the count it reads (``count_key``, a key of
    :meth:`ShippingCarrierInterface.attention_counts`), how it looks, and
    the changelist (``model``, filtered by the ``query`` string) it opens."""

    count_key: str
    icon: str
    tone: str
    title: StrOrPromise
    description: StrOrPromise
    model: str
    query: str


@dataclass(frozen=True)
class OrderShipmentField:
    """A carrier's own field on the order detail: its shipment, in the
    shape of its detail serializer, null for an order another carrier
    ships. The serializer types the field in the OpenAPI schema; the value
    is :meth:`ShippingCarrierInterface.serialize_shipment`'s."""

    name: str
    serializer: type[BaseSerializer]
    help_text: str


# The refusals of a pickup point at checkout, one text each for every
# carrier: the serializer's field errors and the carriers'
# ``validate_order_payload`` say the same.
PICKUP_POINT_UNAVAILABLE = _(
    "This pickup point is not available. Choose another one."
)
PICKUP_POINT_IN_ANOTHER_COUNTRY = _(
    "This pickup point is not in the delivery country. Choose another one."
)


PICKUP_POINT_TOO_SMALL = _(
    "This locker has no compartment for your order's size. Choose another one."
)


PICKUP_POINT_SIZE_UNKNOWN = _(
    "We cannot confirm yet that this locker takes your order's size. "
    "Choose another one."
)


class PickupPointUnfit(ValueError):
    """The pickup point cannot be confirmed to take the order's parcel: no
    order is sent there (``assert_pickup_point_fits``). ``code`` is the
    stable code the API answers with, ``message`` what the shopper reads."""

    code: str
    message: Any


class PickupPointTooSmall(PickupPointUnfit):
    """The pickup point lists the compartment sizes it has, and not the
    order's."""

    code = "pickup_point_too_small"
    message = PICKUP_POINT_TOO_SMALL


class PickupPointSizeUnknown(PickupPointUnfit):
    """The pickup point's compartment sizes are not known yet (a locker the
    carrier's size list does not hold yet): whether it takes the order
    cannot be told."""

    code = "pickup_point_size_unknown"
    message = PICKUP_POINT_SIZE_UNKNOWN


class PickupPointInAnotherCountry(ValueError):
    """The pickup point is in another country than the order's: no order
    is sent there under the order's country (``deliverable_pickup_point``).
    A resync cannot change that, so none runs."""


class PickupPointUnknown(ValueError):
    """The carrier's catalogue does not hold the address of the pickup
    point a shipment goes to, or not of that exact point. Its daily sync
    may add it, so a receipt that names it is tried again
    (``order.tasks.retry_receipts_with_mydata``)."""


class ShippingCarrierInterface(ABC):
    """Abstract adapter every shipping provider must implement.

    Methods may be no-ops (e.g. ``fetch_tracking_events`` for a
    webhook-driven provider that already has the events in DB), but
    every method must exist so callers can dispatch unconditionally.
    """

    code: ClassVar[str]

    # The Django app label of a carrier that is an app of its own (it has
    # models and admin pages); ``None`` for a carrier built into
    # ``shipping`` (the flat rate). A store that does not use the carrier
    # keeps its app's pages out of the sidebar and the ⌘K palette.
    app_label: ClassVar[str | None] = None

    # The URL conf of this carrier's storefront API (its pickup points, its
    # shipment actions), mounted under ``api/v1/`` on a store's host with
    # ``shipping.urls``; ``None`` for a carrier with no API of its own.
    api_urlconf: ClassVar[str | None] = None

    # The URL conf of the endpoints the courier itself calls (a webhook),
    # mounted at the root of both hosts, unprefixed by a language
    # (``shipping.webhook_urls``): its paths are the ones registered with
    # the courier. ``None`` for a carrier that calls us nothing.
    webhook_urlconf: ClassVar[str | None] = None
    # The reverse one-to-one from ``Order`` to this carrier's shipment row
    # (``AcsShipment.order``'s ``related_name``), ``None`` for a carrier
    # with no row of its own. The order lists join it, and
    # :meth:`shipment_for_order` reads it.
    shipment_relation: ClassVar[str | None] = None

    # What the order detail reads of this carrier, as prefetch lookups
    # on ``Order``: the shipment relation, and the rows below it its
    # detail serializer reads.
    order_detail_prefetches: ClassVar[tuple[str, ...]] = ()

    # The order changelist's search fields and list filters this carrier
    # adds, as lookups on ``Order`` (``boxnow_shipment__parcel_id``).
    admin_order_search_fields: ClassVar[tuple[str, ...]] = ()
    admin_order_list_filters: ClassVar[tuple[str, ...]] = ()

    # The models whose writes change :meth:`attention_counts`, as
    # ``app_label.Model``: a write clears the dashboard's cached counts.
    attention_models: ClassVar[tuple[str, ...]] = ()

    # The names of :meth:`checkout_fields` that name the pickup point the
    # shopper chose (a locker, a station). The checkout quote takes them
    # as order creation does, and the cart digest covers them
    # (``order.checkout.quote.ShippingChoice``), so an order goes to the
    # pickup point it was quoted for.
    location_keys: ClassVar[tuple[str, ...]] = ()

    # Where this carrier's own fields sit among the carriers' in an API
    # body (the checkout's :meth:`checkout_fields`, the order detail's
    # :meth:`order_shipment_field`): lower first, carriers that tie in
    # the order they registered (:func:`providers_by_field_rank`).
    field_rank: ClassVar[int] = 0

    # The courier's public tracking page of a parcel, ``{number}`` its
    # tracking number (``Order.tracking_number``, recorded with this
    # carrier's ``code``), for the order's tracking link; ``None`` for a
    # carrier with no such page.
    tracking_url_template: ClassVar[str | None] = None

    @abstractmethod
    def create_shipment(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Create or fetch the provider's shipment row for ``order``.

        Idempotent: must return the existing shipment when one is already
        attached to the order.
        """

    @abstractmethod
    def cancel_shipment(self, shipment: Any, *, reason: str = "") -> None:
        """Cancel the shipment via the provider's API."""

    def can_cancel_shipment(self, shipment: Any) -> bool:
        """Whether the carrier would still accept a cancel of ``shipment``.

        Judged from local data only — never a carrier call — so it is
        safe on every order read. It is the same rule the carrier's own
        :meth:`cancel_shipment` applies before it contacts the courier,
        so a ``False`` here means the cancel would be refused. ``True``
        is not a promise: only the carrier's answer is final.

        Default ``True``: a carrier with no cancel rule of its own (the
        merchant's own delivery) never refuses.
        """
        return True

    def canceled_shipment_q(self) -> Q | None:
        """Orders whose shipment with this carrier is canceled, as a ``Q``
        on ``Order``, for a carrier that leaves the order open behind it.

        Such an order ships nothing until staff create a new shipment or
        cancel the order, so it needs follow-up (``order.lifecycle.follow_up``).
        Default ``None``: a carrier that cancels the order together with
        its canceled shipment (ACS, ``cancel_for_dead_shipment``) never
        leaves one waiting.
        """
        return None

    @abstractmethod
    def has_label(self, shipment: Any) -> bool:
        """Whether the carrier has a label for ``shipment`` yet.

        Judged from local data only, never a carrier call. A shipment
        row exists from checkout, but its label exists only once the
        carrier has booked it (an ACS voucher, a BoxNow parcel), so a
        caller asks this before :meth:`fetch_label_bytes`.
        """

    @abstractmethod
    def fetch_label_bytes(self, shipment: Any) -> bytes:
        """Return the raw label PDF bytes (provider-format may vary).

        Raises when :meth:`has_label` is false.
        """

    @abstractmethod
    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        """Refresh tracking events from the provider.

        Polling providers (ACS) hit their API; push providers (BoxNow)
        return an empty list because events arrive via webhook.
        """

    def shipment_for_order(self, order: Order) -> Any | None:
        """The carrier's shipment row of ``order`` (its
        :attr:`shipment_relation`), or ``None``."""
        if self.shipment_relation is None:
            return None
        return getattr(order, self.shipment_relation, None)

    def shipment_state(self, shipment: Any) -> tuple[str, StrOrPromise] | None:
        """The state of ``shipment`` as the admin labels it: its value
        (a key of :meth:`shipment_state_variants`) and its display.

        Default: none, for a carrier whose shipments have no states.
        """
        return None

    def last_reported_at(self, shipment: Any) -> datetime | None:
        """When the carrier last reported on the parcel of ``shipment``
        (a tracking event, a webhook), what the follow-up of a waiting
        order reads (``order.lifecycle.follow_up``).

        Default: never, for a carrier that reports nothing.
        """
        return None

    @abstractmethod
    def holds_the_parcel(self, shipment: Any) -> bool:
        """Whether the carrier has accepted the parcel of *shipment*: its
        voucher or parcel exists at the carrier, not only the local row a
        checkout writes before asking for one. What a receipt with
        movement indication waits for (``order.accounting.invoicing.
        receipt_issuable``)."""

    def pickup_point_address(self, shipment: Any) -> PostalAddress | None:
        """The address of the pickup point (a locker, a station) the parcel
        goes to, or ``None`` when it goes to the buyer's own address.

        The receipt's delivery address (``order.accounting.movement``).
        Raises ``ValueError`` when the parcel goes to a pickup point whose
        address is not known.
        """
        return None

    def erase_recipient_data(
        self, order_ids: list[int], *, buyer_values: dict[int, list[str]]
    ) -> dict[str, int]:
        """Remove the recipient's data from this carrier's records of the
        given orders, for GDPR erasure (``order.privacy``): the copies of
        the order's name, address, phone and notes it keeps on rows of its
        own, which survive the order's anonymisation. *buyer_values* are
        each order's email, name, phone and typed cancel reason, to redact
        from free text staff wrote (empty when the order was erased
        earlier). Returns a tally of the rows touched, by kind.

        Default: nothing to remove, for a carrier that keeps no records of
        its own (the flat rate).
        """
        return {}

    def orders_written_since(self, since: datetime) -> set[int]:
        """The orders this carrier wrote a record of since *since*, read
        from the rows written since then: what the daily sweep of erased
        orders clears again (``order.privacy.erase_late_carrier_data``),
        so data that arrives after an erasure, the parcel's last event
        included, does not stay.

        Default: none, for a carrier that keeps no records of its own.
        """
        return set()

    def recipient_data(self, order_ids: list[int]) -> dict[int, dict]:
        """What this carrier reported about the recipient of each of the
        given orders and :meth:`erase_recipient_data` clears, for a data
        export (``user.services.gdpr``), by order id. The copies of the
        order's own data it keeps are not repeated: the export lists the
        order.

        Default: nothing, for a carrier that keeps no records of its own.
        """
        return {}

    def address_of_pickup_point(
        self, location: Mapping[str, str]
    ) -> PostalAddress:
        """The address of the pickup point ``location`` names (this
        carrier's ``location_keys``), from its catalogue now.

        A pickup-point order is delivered there, so checkout stores it as
        the order's delivery address (``OrderCreateFromCartSerializer``).
        Raises ``PickupPointUnknown`` when the catalogue does not hold it;
        a carrier with no pickup points holds none.
        """
        raise PickupPointUnknown(f"Carrier {self.code} has no pickup points.")

    def pickup_point_errors(
        self, field: str, location: Mapping[str, str], *, country_code: str
    ) -> dict[str, list[str]]:
        """``validate_order_payload``'s errors for the point *location*
        names: :meth:`deliverable_pickup_point`'s rule, as field errors."""
        try:
            self.deliverable_pickup_point(location, country_code=country_code)
        except PickupPointInAnotherCountry:
            return {field: [str(PICKUP_POINT_IN_ANOTHER_COUNTRY)]}
        except PickupPointUnknown:
            return {field: [str(PICKUP_POINT_UNAVAILABLE)]}
        return {}

    def deliverable_pickup_point(
        self, location: Mapping[str, str], *, country_code: str
    ) -> PostalAddress:
        """The address of the pickup point *location* names when an order
        for *country_code* can be sent there now: the catalogue holds it,
        it is active (a sync marks a point the carrier withdrew inactive
        and keeps its row), and it is in that country.

        The order-time rule; :meth:`address_of_pickup_point` stays the
        receipt's, which must still name a point withdrawn after the order.
        Raises ``PickupPointUnknown`` for a point missing or withdrawn (a
        resync may bring it back) and ``PickupPointInAnotherCountry``.
        """
        raise PickupPointUnknown(f"Carrier {self.code} has no pickup points.")

    def refresh_pickup_points(self, *, country_code: str) -> None:
        """Fetch the carrier's catalogue of pickup points again, now,
        through the path its scheduled sync takes, for checkout to find
        a point the last sync did not hold (``ShippingService.
        pickup_point_address``). A carrier with no pickup points has none
        to fetch."""
        return

    @abstractmethod
    def serialize_shipment(
        self, shipment: Any, *, context: Mapping[str, Any]
    ) -> dict | None:
        """Return the provider's detail-serializer dict for ``shipment``."""

    @abstractmethod
    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Return field-level errors for the create-order payload (DRF format).

        Empty dict when the payload is valid for this provider+kind.
        """

    def assert_parcel_fits(
        self,
        *,
        kind: ShippingKind,
        items: Iterable[tuple[Any, int]],
    ) -> None:
        """Raise ``ShippingParcelTooLargeError`` when ``items`` cannot ship.

        ``items`` are ``(product, quantity)`` pairs. Called next to the
        rate's weight cap, so a carrier with fixed parcel sizes (a
        locker) refuses at checkout rather than at the courier.
        Default: no size limit.
        """
        return

    def parcel_size(
        self,
        *,
        kind: ShippingKind,
        items: Iterable[tuple[Any, int]],
    ) -> int | None:
        """The compartment size ``items`` pack into for this carrier and
        kind, or ``None`` when the carrier has no sizes or they fit none.
        Default: none."""
        return None

    def assert_pickup_point_fits(
        self,
        location: Mapping[str, str],
        *,
        items: Iterable[tuple[Any, int]],
    ) -> None:
        """Raise :class:`PickupPointUnfit` when the pickup point
        *location* names cannot take ``items`` packed as the carrier
        packs them. Called once the point is known to exist
        (:meth:`deliverable_pickup_point`). Default: every point fits."""
        return

    def live_quote(
        self,
        *,
        rate: ShippingRate,
        country_code: str,
        region_id: str | None = None,
        weight_grams: int | None = None,
        currency: str,
        cached_only: bool = False,
    ) -> Decimal | None:
        """Return a live-quoted price that overrides ``rate.price``.

        ``cached_only`` is for a caller that holds row locks (order
        creation): the carrier is not asked. An adapter that prices live
        answers from what it resolved earlier, and raises
        :class:`~shipping.exceptions.ShippingLivePriceUnresolvedError`
        when it holds no answer.

        ``rate`` is the resolved ``ShippingRate`` for this
        (provider, zone, kind) — the price, free-shipping threshold
        and weight cap all live there now; this hook exists only for a
        carrier whose tariff varies by destination or weight bracket
        beyond what a single stored price can express (ACS's weight-
        banded ``ACS_Price_Calculation``).

        ``weight_grams`` is the total cart weight at quote time.
        ``None`` means caller has no weight info — carriers fall back
        to a sensible floor (ACS uses its 0.5 kg minimum chargeable
        weight).

        Default ``None``: the carrier has no live pricing of its own,
        so :meth:`shipping.services.ShippingService.quote` uses
        ``rate.price`` unchanged. Override only when the provider
        genuinely re-prices per call (e.g. ACS with
        ``ACS_DYNAMIC_PRICING_ENABLED``); a transient failure here MUST
        return ``None`` rather than raise, so a live-pricing outage
        falls back to the stored rate instead of blocking checkout.
        """
        return None

    def is_kind_enabled(
        self, kind: ShippingKind, *, country_code: str | None
    ) -> bool:
        """Return whether the provider's ``kind`` is enabled in checkout.

        ``country_code`` is the delivery country (ISO alpha-2), for a
        provider whose ``kind`` depends on data it holds per country. It
        is ``None`` when the caller does not know the country yet, which
        means "any country": answer True when the kind is enabled for at
        least one, so the caller treats the provider as a candidate
        rather than dropping it. The argument has no default so that a
        caller states which of the two it means.

        Defaults to True so providers that only need the
        ``ShippingProvider.is_active`` master switch don't need to
        override this hook.  Override when a provider gates a kind on
        its own state — e.g. ACS offers locker pickup only once it holds
        stations for the country, independently of home delivery.
        """
        return True

    def kind_enabled_countries(
        self, kind: ShippingKind, country_codes: Iterable[str]
    ) -> frozenset[str]:
        """The upper-case ``country_codes`` where ``kind`` is enabled,
        :meth:`is_kind_enabled` answered for a set at once (the store's
        shippable countries, its home coverage).

        Defaults to asking :meth:`is_kind_enabled` per country. Override
        when the answer is a query, so a set costs one (ACS's stations).
        """
        return frozenset(
            code
            for code in {code.upper() for code in country_codes}
            if self.is_kind_enabled(kind, country_code=code)
        )

    def create_demo_shipment(
        self,
        order: Order,
        row: ShipmentRow,
        *,
        placed_at: datetime,
        now: datetime,
        collect: Money,
        weight_grams: int,
    ) -> Any:
        """Write the shipment a demo account's order shipped under, as
        this carrier's clients would have after a voucher and a few polls
        (``shipping.demo``): its row in ``row.state`` and its events, timed
        from *placed_at* to *now*, the courier collecting *collect*.

        Default: refused, for a carrier with no shipment rows to write.
        """
        raise NotImplementedError(
            f"The {self.code} carrier has no demo shipments."
        )

    def admin_pages(self) -> AdminPages:
        """The admin pages this carrier adds to the sidebar.

        Default: none, for a carrier with no admin of its own.
        """
        return AdminPages()

    def admin_order_inline(self) -> type[InlineModelAdmin] | None:
        """The inline the order change page shows for an order this
        carrier ships: its shipment row.

        Default: none, for a carrier with no row of its own.
        """
        return None

    def checkout_fields(self) -> dict[str, Field]:
        """The fields a checkout body takes for this carrier, by name, new
        instances on every call: its pickup point (the
        :attr:`location_keys`, on every body that names a delivery: the
        quote and the order) and its options for the
        order alone (order creation's body). Order creation hands the
        values to :meth:`create_shipment` and
        :meth:`validate_order_payload` as the payload.

        Default: none, for a carrier with nothing to choose.
        """
        return {}

    def order_shipment_field(self) -> OrderShipmentField | None:
        """This carrier's own field on the order detail
        (``OrderDetailSerializer``), next to the generic ``shipment``.

        Default: none.
        """
        return None

    def shipment_state_variants(self) -> Mapping[str, str]:
        """The colour (an Unfold label variant: ``default``, ``info``,
        ``success``, ``warning``, ``danger``) of each state value of this
        carrier's shipments, for the admin's state labels.

        Default: none, for a carrier whose shipments have no states.
        """
        return {}

    def attention_counts(self, now: datetime) -> dict[str, int]:
        """How much of this carrier's work waits for a person, by the keys
        :meth:`attention_rows` reads: the same rules its alert emails use,
        so a store that switches an email off still sees the work.

        Default: none, for a carrier that leaves nothing waiting.
        """
        return {}

    def attention_rows(
        self, counts: Mapping[str, int]
    ) -> tuple[AttentionRow, ...]:
        """The dashboard lines for the work :meth:`attention_counts`
        counted, in the order they are listed. A row is drawn only while
        its count is above zero, and only when the viewer may open its
        page.

        Default: none.
        """
        return ()

    def create_shipment_row(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
        items: list[tuple[Any, int]] | None = None,
    ) -> None:
        """Persist the provider-specific shipment row for ``order``.

        Called from the order-creation paths *synchronously* (the
        Celery task that talks to the courier API runs later, after
        payment succeeds).  Idempotent: a no-op when a shipment row
        already exists.

        Default implementation is a no-op so providers that only
        register for capability advertising (no shipment row) don't
        have to override.  BoxNow + ACS implement this to drop their
        per-Order child rows during checkout.
        """
        return

    def dispatch_create_shipment_task(
        self, order: Order, *, schema_name: str | None = None
    ) -> None:
        """Enqueue the provider's create-shipment Celery task.

        Called from ``OrderService.handle_payment_succeeded`` after a
        successful payment.  Default is a no-op so providers without
        an asynchronous create step (e.g. local-only fulfilment
        carriers) don't have to override.

        ``schema_name`` is the tenant schema captured by the caller
        BEFORE the deferring ``on_commit`` — overriders publish through
        ``tenant.celery.dispatch_on_commit(task, [order.id],
        schema_name=schema_name)`` so the worker binds the right tenant
        even when on_commit fires after the request's schema context has
        unwound, and so a broker failure (the payment is already
        committed) is kept as a ``DeferredDispatch`` for the sweep to
        publish again instead of leaving a paid order with no shipment.
        A bare ``apply_async`` here raises into the payment webhook.
        """
        return

    def supported_settlements(
        self, kind: ShippingKind
    ) -> frozenset[PaySettlement] | None:
        """Which settlements this carrier can physically perform.

        ``None`` (the default) means "no protocol-level constraint" —
        the carrier accepts anything the platform offers.

        This is a *capability declaration*, not a preference. A courier
        can take cash at a door; an unattended locker cannot, because
        nobody is there to take it. Getting this wrong is not a styling
        bug — it mints a voucher for the wrong
        commercial product, which is exactly what happened when
        ``PayWay.is_cash_on_delivery`` was the only discriminator
        available (see ``PaySettlement``).

        Merchant *preferences* — "we have PAY ON THE GO but don't want
        to offer it this month" — belong in ``PayWayShippingExclusion``
        rows, which ``PayWayService.filter_by_carrier`` applies on top
        of this. Do not encode preferences here; they would need a
        redeploy to change.
        """
        return None

    def filter_pay_ways(self, queryset, *, kind: ShippingKind):
        """Return a PayWay queryset filtered by this carrier's rules.

        Derived from :meth:`supported_settlements` — carriers declare
        capability and this turns it into a queryset, so no carrier has
        to hand-write queryset surgery.

        Overriding this directly is almost always wrong: the previous
        implementation of that idea excluded *all offline* pay-ways for
        BoxNow, which broke PAY ON THE GO and had to be reverted
        (``891d5663``). Declare settlements instead — "offline" is not
        a carrier capability, it is three different ones.
        """
        supported = self.supported_settlements(kind)
        if supported is None:
            return queryset
        return queryset.filter(
            settlement__in=sorted(s.value for s in supported)
        )


# Sent once a carrier is registered, so a listener that needs every
# carrier (the dashboard's cache invalidation) can wait for the ones that
# register after it was set up.
carrier_registered = Signal()

# Module-level adapter registry. Populated by @register_provider at
# AppConfig.ready() time. Lookup via get_provider("acs") etc.
_REGISTRY: dict[str, ShippingCarrierInterface] = {}


def register_provider(
    cls: type[ShippingCarrierInterface],
) -> type[ShippingCarrierInterface]:
    """Class decorator that instantiates and registers a carrier adapter.

    Re-registering the same code overwrites the previous adapter — that
    is intentional so test factories can swap in mock adapters.
    """
    code = cls.code
    if not code:
        raise ValueError(
            f"{cls.__name__} must declare a non-empty class-level 'code' "
            "before @register_provider."
        )
    _REGISTRY[code] = cls()
    carrier_registered.send(sender=cls)
    return cls


def get_provider(code: str) -> ShippingCarrierInterface:
    """Return the adapter registered under ``code`` or raise."""
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise ShippingProviderNotFoundError(code) from exc


def is_registered(code: str) -> bool:
    """Return True when an adapter is registered under ``code``."""
    return code in _REGISTRY


def registered_providers() -> tuple[ShippingCarrierInterface, ...]:
    """Every registered adapter, for a rule that asks each carrier."""
    return tuple(_REGISTRY.values())


def providers_by_field_rank() -> list[ShippingCarrierInterface]:
    """Every registered adapter by its
    :attr:`~ShippingCarrierInterface.field_rank`, for an API body that
    lists fields of each carrier."""
    return sorted(
        registered_providers(), key=lambda carrier: carrier.field_rank
    )


def shipment_relations() -> tuple[str, ...]:
    """Every carrier's :attr:`~ShippingCarrierInterface.shipment_relation`,
    for a queryset of orders that joins their shipments."""
    return tuple(
        carrier.shipment_relation
        for carrier in registered_providers()
        if carrier.shipment_relation is not None
    )


def order_detail_prefetches() -> tuple[str, ...]:
    """Every carrier's
    :attr:`~ShippingCarrierInterface.order_detail_prefetches`, for a
    queryset of orders the detail serializer reads."""
    return tuple(
        lookup
        for carrier in registered_providers()
        for lookup in carrier.order_detail_prefetches
    )
