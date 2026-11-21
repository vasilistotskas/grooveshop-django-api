"""BoxNow adapter for the generic shipping abstraction.

Registers under code ``"boxnow"`` at ``ShippingBoxNowConfig.ready()``
time.  All public methods delegate to the existing ``BoxNowService``
class so behaviour is unchanged from the pre-abstraction code path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from django.db.models import Q

from pay_way.enum.settlement import PaySettlement
from shipping.enum import ShippingKind
from shipping.interfaces import (
    PickupPointInAnotherCountry,
    PickupPointUnknown,
    PostalAddress,
    ShippingCarrierInterface,
    register_provider,
)
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.exceptions import BoxNowUnsupportedSettlementError
from shipping_boxnow.packing import derive_compartment, resolve_compartment_size

if TYPE_CHECKING:
    from datetime import datetime

    from order.models.order import Order
    from shipping_boxnow.models import BoxNowShipment

logger = logging.getLogger(__name__)


@register_provider
class BoxNowCarrier(ShippingCarrierInterface):
    code: ClassVar[str] = "boxnow"

    # Carrier-specific request-body keys popped before Order.create().
    payload_keys: ClassVar[tuple[str, ...]] = ("boxnow_locker_id",)
    location_keys: ClassVar[tuple[str, ...]] = ("boxnow_locker_id",)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_shipment(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any] | None = None,
    ) -> BoxNowShipment:
        from shipping_boxnow.services import BoxNowService

        return BoxNowService.create_shipment_for_order(order)

    def cancel_shipment(self, shipment: Any, *, reason: str = "") -> None:
        from shipping_boxnow.services import BoxNowService

        BoxNowService.cancel_shipment(shipment, reason=reason)

    def can_cancel_shipment(self, shipment: Any) -> bool:
        from shipping_boxnow.services import BoxNowService

        return BoxNowService.can_cancel_shipment(shipment)

    def canceled_shipment_q(self) -> Q:
        # A canceled parcel leaves its order as it is
        # (``BoxNowService._apply_order_status_transition``).
        return Q(boxnow_shipment__parcel_state=BoxNowParcelState.CANCELED)

    def has_label(self, shipment: Any) -> bool:
        return bool(shipment.parcel_id)

    def fetch_label_bytes(self, shipment: Any) -> bytes:
        from shipping_boxnow.services import BoxNowService

        return BoxNowService.fetch_label_bytes(shipment)

    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        # BoxNow is webhook-driven — events arrive via the webhook
        # receiver, not by polling. No-op here.
        return []

    def shipment_for_order(self, order: Order):
        return getattr(order, "boxnow_shipment", None)

    def holds_the_parcel(self, shipment: Any) -> bool:
        """BOX NOW holds a parcel for the shipment (created, or taken over
        after a P410)."""
        return shipment.has_parcel

    def pickup_point_address(self, shipment: Any) -> PostalAddress:
        """The address of the shipment's locker
        (:meth:`address_of_pickup_point`)."""
        return self.address_of_pickup_point(
            {"boxnow_locker_id": shipment.locker_external_id}
        )

    def refresh_pickup_points(self, *, country_code: str) -> None:
        """The daily ``sync_boxnow_lockers`` run, now: BOX NOW lists its
        destinations whole, with no lookup of one by id."""
        from shipping_boxnow.services import BoxNowService

        BoxNowService.sync_lockers()

    def address_of_pickup_point(
        self, location: Mapping[str, str]
    ) -> PostalAddress:
        """The locker's address as BOX NOW lists it. Its destinations carry
        no city field: the city is ``addressLine2`` (BOX NOW API manual
        v7.2, the ``/api/v1/destinations`` example: ``"addressLine2":
        "ΑΘΗΝΑ"``). Read from the catalogue now, so a locker a later sync
        added is found (``PickupPointUnknown`` until then)."""
        return self._address(self._locker(location), location)

    def deliverable_pickup_point(
        self, location: Mapping[str, str], *, country_code: str
    ) -> PostalAddress:
        locker = self._locker(location)
        if locker is not None and not locker.is_active:
            raise PickupPointUnknown(
                f"BOX NOW locker {locker.external_id} is withdrawn."
            )
        address = self._address(locker, location)
        if (locker.country_code or "").upper() != country_code.upper():
            raise PickupPointInAnotherCountry(
                f"BOX NOW locker {locker.external_id} is in "
                f"{locker.country_code}, not {country_code}."
            )
        return address

    @staticmethod
    def _locker(location: Mapping[str, str]):
        from shipping_boxnow.models import BoxNowLocker

        external_id = location.get("boxnow_locker_id") or ""
        return BoxNowLocker.objects.filter(external_id=external_id).first()

    @staticmethod
    def _address(locker, location: Mapping[str, str]) -> PostalAddress:
        if locker is None or not (locker.postal_code and locker.address_line_2):
            raise PickupPointUnknown(
                "The address of BOX NOW locker "
                f"{location.get('boxnow_locker_id') or ''} is not known."
            )
        return PostalAddress(
            street=locker.address_line_1,
            street_number="",
            postal_code=locker.postal_code,
            city=locker.address_line_2,
        )

    def erase_recipient_data(
        self, order_ids: list[int], *, buyer_values: dict[int, list[str]]
    ) -> dict[str, int]:
        """The BOX NOW records of the orders, cleared of the recipient:
        each shipment's ``metadata`` at ``BoxNowShipment.PERSONAL_METADATA``
        (the rest stays), and each parcel event's text and the raw body's
        ``BoxNowParcelEvent.PERSONAL_RAW`` (the customer BOX NOW names, its
        text); the events stay. Nothing here is free text staff wrote, so
        *buyer_values* is not read. Run again for orders already erased, it
        clears the events that arrived since."""
        from core.utils.json_paths import drop_paths
        from shipping_boxnow.models import BoxNowShipment
        from shipping_boxnow.models.parcel_event import BoxNowParcelEvent

        scrubbed = 0
        for shipment in BoxNowShipment.objects.filter(order_id__in=order_ids):
            metadata = shipment.metadata or {}
            if drop_paths(metadata, BoxNowShipment.PERSONAL_METADATA):
                BoxNowShipment.objects.filter(pk=shipment.pk).update(
                    metadata=metadata
                )
                scrubbed += 1

        events = 0
        for event in BoxNowParcelEvent.objects.filter(
            shipment__order_id__in=order_ids
        ):
            raw = (
                event.raw_payload if isinstance(event.raw_payload, dict) else {}
            )
            dropped = drop_paths(raw, BoxNowParcelEvent.PERSONAL_RAW)
            if event.additional_information or dropped:
                event.additional_information = ""
                event.raw_payload = raw
                event.save(
                    update_fields=["additional_information", "raw_payload"]
                )
                events += 1

        return {
            "carrier_pii_scrubbed": scrubbed,
            "carrier_events_scrubbed": events,
        }

    def orders_written_since(
        self, order_ids: list[int], since: datetime
    ) -> set[int]:
        """The orders BOX NOW wrote a record of since *since*: a shipment
        row or a parcel event."""
        from shipping_boxnow.models import BoxNowShipment
        from shipping_boxnow.models.parcel_event import BoxNowParcelEvent

        return set(
            BoxNowShipment.objects.filter(
                order_id__in=order_ids, updated_at__gte=since
            ).values_list("order_id", flat=True)
        ) | set(
            BoxNowParcelEvent.objects.filter(
                shipment__order_id__in=order_ids, created_at__gte=since
            ).values_list("shipment__order_id", flat=True)
        )

    def recipient_data(self, order_ids: list[int]) -> dict[int, dict]:
        """The text BOX NOW sent with each order's parcel events, for a
        data export (what :meth:`erase_recipient_data` clears)."""
        from shipping_boxnow.models.parcel_event import BoxNowParcelEvent

        data: dict[int, dict] = {}
        for order_id, text in (
            BoxNowParcelEvent.objects.filter(shipment__order_id__in=order_ids)
            .exclude(additional_information="")
            .order_by("event_time")
            .values_list("shipment__order_id", "additional_information")
        ):
            entry = data.setdefault(order_id, {})
            entry.setdefault("boxnow_event_texts", []).append(text)
        return data

    def serialize_shipment(
        self, shipment: Any, *, context: Mapping[str, Any]
    ) -> dict | None:
        from shipping_boxnow.serializers.shipment import (
            BoxNowShipmentDetailSerializer,
        )

        return BoxNowShipmentDetailSerializer(shipment, context=context).data

    # ------------------------------------------------------------------
    # Validation (called by order/services._validate_address_data)
    # ------------------------------------------------------------------

    def supported_settlements(
        self, kind: ShippingKind
    ) -> frozenset[PaySettlement] | None:
        """BoxNow collects for a locker parcel, but never cash.

        Nobody meets the shopper: the parcel goes into a compartment
        they open themselves. So ``COURIER_CASH`` is physically
        impossible here — there is no courier to hand notes to and its
        money would simply never be collected.

        ``CARRIER_TERMINAL`` (BOX NOW Αντικαταβολή, marketed in English
        as PAY ON THE GO) is the locker's own collect-before-pickup
        product and is the only one allowed. Note it is NOT a card
        reader on the locker, as this docstring used to claim: BoxNow
        sends a Viva Wallet link after dispatch and activates the
        pickup PIN once the payment clears.

        This is BoxNow's own stated requirement: PAY ON THE GO must be
        a distinct option, and traditional courier COD must not be
        selectable when the delivery method is a locker.

        Note what this replaces. Before ``settlement`` existed the only
        discriminator was ``is_cash_on_delivery``, true for both
        products — so a shopper picking the generic "Αντικαταβολή
        (+1,99 €)" was minted as BoxNow COD *and* charged a
        cash-handling surcharge, then paid by card at a terminal with
        no cash and no courier involved.
        """
        if kind != ShippingKind.PICKUP_POINT:
            return None
        return frozenset(
            {
                PaySettlement.ONLINE,
                PaySettlement.CARRIER_TERMINAL,
                PaySettlement.OFFLINE_TRANSFER,
            }
        )

    # ------------------------------------------------------------------
    # Per-kind feature gating
    # ------------------------------------------------------------------

    def is_kind_enabled(
        self, kind: ShippingKind, *, country_code: str | None
    ) -> bool:
        """Gate BoxNow availability on tenant credentials.

        An unconfigured tenant (no ``Tenant.box_now_*`` credentials)
        gets no BoxNow kind at all — the locker widget and pickup-point
        option must never be offered when we can't actually call the
        BoxNow API on the tenant's behalf.
        """
        from shipping_boxnow.services import is_configured

        return is_configured()

    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Locker required for a locker order, and deliverable: held by
        the catalogue, active and in the delivery country
        (``deliverable_pickup_point``). Order creation refuses it first,
        field-coded and after one resync of the catalogue
        (``OrderCreateFromCartSerializer._deliver_to_pickup_point``).
        """
        from django.utils.translation import gettext_lazy as _

        if kind != ShippingKind.PICKUP_POINT:
            return {}

        locker_id = payload.get("boxnow_locker_id")
        if not locker_id:
            return {
                "boxnow_locker_id": [
                    str(
                        _(
                            "Select a BOX NOW locker before placing an "
                            "order with locker delivery."
                        )
                    )
                ]
            }

        return self.pickup_point_errors(
            "boxnow_locker_id",
            {"boxnow_locker_id": str(locker_id)},
            country_code=payload.get("country_id") or "",
        )

    def assert_parcel_fits(
        self,
        *,
        kind: ShippingKind,
        items: Iterable[tuple[Any, int]],
    ) -> None:
        """Refuse a cart whose measured items fit no BoxNow compartment."""
        if kind != ShippingKind.PICKUP_POINT:
            return
        derive_compartment(items)

    # ------------------------------------------------------------------
    # Order-creation hooks (Phase 3 abstraction)
    # ------------------------------------------------------------------

    def create_shipment_row(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
        items: list[tuple[Any, int]] | None = None,
    ) -> None:
        """Persist the BoxNow shipment row for ``order``.

        Lifted from the inline blocks that previously lived in each
        of the two ``OrderService.create_order_from_cart*`` paths.
        Idempotent — re-runs are no-ops because the row already exists.

        Reads:
        * ``boxnow_locker_id`` (string)

        Computes the parcel weight from the cart line items so the
        BoxNow voucher PDF prints the real weight (BoxNow tariffs by
        weight bracket), and the compartment size from their
        dimensions (``shipping_boxnow.packing``).
        """
        from shipping.utils import compute_total_weight_grams
        from shipping_boxnow.models import BoxNowLocker, BoxNowShipment

        if kind != ShippingKind.PICKUP_POINT:
            return
        if BoxNowShipment.objects.filter(order=order).exists():
            return

        locker_id = payload.get("boxnow_locker_id", "") or ""
        compartment_size = resolve_compartment_size(items)

        weight_grams = 0
        if items is not None:
            weight_grams = compute_total_weight_grams(items)

        locker = (
            BoxNowLocker.objects.filter(external_id=locker_id).first()
            if locker_id
            else None
        )

        # Derive paymentMode from the order's SETTLEMENT, not from a
        # broad "is this offline?" boolean.
        #
        # BoxNow's wire enum has only ``prepaid`` and ``cod``, where
        # ``cod`` means "collect at the locker" — the terminal takes a
        # card. So ``cod`` IS the right wire value for PAY ON THE GO;
        # the historical defect was never the enum, it was that two
        # different commercial products both routed to it.
        #
        # Only ``CARRIER_TERMINAL`` may collect here. ``COURIER_CASH``
        # cannot: no POS on a locker round, no cash accepted. It is
        # filtered out of the checkout by ``supported_settlements``, so
        # reaching this branch means the pay-way bypassed that gate —
        # raise rather than quietly minting a voucher that charges a
        # cash-handling fee for a courier who is not involved.
        # ``OFFLINE_TRANSFER`` ships PREPAID: the shopper pays us
        # directly, so BoxNow must collect nothing or it double-charges.
        #
        # ``amountToBeCollected`` is set lazily in
        # ``BoxNowService.create_shipment_for_order`` (Phase 1) once the
        # order's items + shipping_price + fees are fully persisted —
        # ``order.total_price`` is 0 here because items are saved AFTER
        # this row.
        from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode

        pay_way = getattr(order, "pay_way", None)
        settlement = (
            PaySettlement(pay_way.settlement) if pay_way is not None else None
        )

        if settlement == PaySettlement.COURIER_CASH:
            raise BoxNowUnsupportedSettlementError(
                order_id=order.id,
                settlement=settlement.value,
            )

        payment_mode = (
            BoxNowPaymentMode.COD
            if settlement == PaySettlement.CARRIER_TERMINAL
            else BoxNowPaymentMode.PREPAID
        )

        BoxNowShipment.objects.create(
            order=order,
            locker_external_id=locker_id,
            locker=locker,
            compartment_size=compartment_size,
            weight_grams=weight_grams,
            payment_mode=payment_mode,
        )

    def dispatch_create_shipment_task(
        self, order: Order, *, schema_name: str | None = None
    ) -> None:
        """Enqueue the BoxNow create-shipment Celery task for ``order``."""
        from shipping_boxnow.tasks import create_boxnow_shipment_for_order

        logger.info(
            "BoxNow dispatch: queued create-shipment for order=%s",
            order.id,
            extra={"order_id": order.id, "carrier": "boxnow"},
        )
        # Stamp the caller-captured tenant schema — see the base
        # method's contract. A broker failure is kept as a
        # ``DeferredDispatch`` instead of leaving a paid order with no
        # shipment.
        from tenant.celery import dispatch_on_commit

        dispatch_on_commit(
            create_boxnow_shipment_for_order,
            [order.id],
            schema_name=schema_name,
        )

    # Pricing is a ``ShippingRate`` row now (boxnow/CY/pickup_point,
    # boxnow/GR/pickup_point, ...) — this adapter has no
    # ``live_quote`` override because BoxNow's tariff is contract-flat
    # per partner, not weight- or destination-banded, so the default
    # "no live pricing" behaviour (``rate.price`` unchanged) is correct.
