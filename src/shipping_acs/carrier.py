"""ACS adapter for the generic shipping abstraction."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from pay_way.enum.settlement import PaySettlement
from shipping.enum import ShippingKind
from shipping.interfaces import (
    PickupPointInAnotherCountry,
    PickupPointUnknown,
    PostalAddress,
    ShippingCarrierInterface,
    register_provider,
)

if TYPE_CHECKING:
    from order.models.order import Order
    from shipping.models import ShippingRate
    from shipping_acs.models import AcsShipment

logger = logging.getLogger(__name__)


@register_provider
class AcsCarrier(ShippingCarrierInterface):
    code: ClassVar[str] = "acs"

    # Carrier-specific request-body keys popped before Order.create().
    # Add a new key here when ACS introduces a new per-order field
    # (Phase 4 added ``acs_charge_type`` for offline-payway override).
    payload_keys: ClassVar[tuple[str, ...]] = (
        "acs_station_external_id",
        "acs_station_branch",
        "acs_charge_type",
        "acs_item_quantity",
    )
    location_keys: ClassVar[tuple[str, ...]] = (
        "acs_station_external_id",
        "acs_station_branch",
    )

    def create_shipment(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any] | None = None,
    ) -> AcsShipment:
        from shipping_acs.services import AcsService

        return AcsService.create_voucher_for_order(order)

    def cancel_shipment(self, shipment: Any, *, reason: str = "") -> None:
        from shipping_acs.services import AcsService

        AcsService.cancel_voucher(shipment, reason=reason)

    def can_cancel_shipment(self, shipment: Any) -> bool:
        from shipping_acs.services import AcsService

        return AcsService.can_cancel_voucher(shipment)

    def has_label(self, shipment: Any) -> bool:
        return bool(shipment.voucher_no)

    def fetch_label_bytes(self, shipment: Any) -> bytes:
        from shipping_acs.services import AcsService

        return AcsService.fetch_label_bytes(shipment)

    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        # Polling-only — caller dispatches via the Celery task.
        return []

    def shipment_for_order(self, order: Order):
        return getattr(order, "acs_shipment", None)

    def holds_the_parcel(self, shipment: Any) -> bool:
        """ACS minted (or staff adopted) the voucher."""
        return bool(shipment.voucher_no)

    def pickup_point_address(self, shipment: Any) -> PostalAddress | None:
        """The station's address, only from the catalogue row of the exact
        station ACS books: the (external_id, branch_code) pair the voucher
        carries, read from the catalogue now, so a pair a later sync added
        is found. ``station_destination`` may be a row of the same area but
        another branch (the code-only fallback when the pair was not in the
        catalogue at checkout), whose address the receipt must not name; a
        booking without a branch names only the row without one, never the
        first station of its area."""

        if shipment.delivery_kind != ShippingKind.PICKUP_POINT:
            return None
        return self.address_of_pickup_point(
            {
                "acs_station_external_id": (
                    shipment.station_destination_external_id
                ),
                "acs_station_branch": shipment.station_branch_destination,
            }
        )

    def refresh_pickup_points(self, *, country_code: str) -> None:
        """``sync_acs_stations`` for the order's country, now: ACS lists
        its stations per country and kind, with no lookup of one by id."""
        from shipping_acs.services import AcsService

        AcsService.sync_stations(country=country_code)

    def address_of_pickup_point(
        self, location: Mapping[str, str]
    ) -> PostalAddress:
        """The address of the exact station ``location`` names: its
        (external_id, branch) pair, a blank branch naming only the row
        without one, read from the catalogue now."""
        return self._address(self._station(location), location)

    def deliverable_pickup_point(
        self, location: Mapping[str, str], *, country_code: str
    ) -> PostalAddress:
        station = self._station(location)
        if station is not None and not station.is_active:
            raise PickupPointUnknown(
                f"ACS station {station.external_id} "
                f"{station.branch_code} is withdrawn."
            )
        address = self._address(station, location)
        if (station.country_code or "").upper() != country_code.upper():
            raise PickupPointInAnotherCountry(
                f"ACS station {station.external_id} {station.branch_code} "
                f"is in {station.country_code}, not {country_code}."
            )
        return address

    @staticmethod
    def _station(location: Mapping[str, str]):
        from shipping_acs.models import AcsStation

        return AcsStation.objects.filter(
            external_id=location.get("acs_station_external_id") or "",
            branch_code=location.get("acs_station_branch") or "",
        ).first()

    @staticmethod
    def _address(station, location: Mapping[str, str]) -> PostalAddress:
        if station is None or not (station.postal_code and station.city):
            raise PickupPointUnknown(
                "The address of ACS station "
                f"{location.get('acs_station_external_id') or ''} "
                f"{location.get('acs_station_branch') or ''} is not known."
            )
        return PostalAddress(
            street=station.address_line_1,
            street_number="",
            postal_code=station.postal_code,
            city=station.city,
        )

    def erase_recipient_data(self, order_ids: list[int]) -> dict[str, int]:
        """The ACS records of the orders, cleared of the recipient:

        - each shipment's ``metadata`` at ``AcsShipment.PERSONAL_METADATA``;
          the request's operational fields, the billing code and the child
          voucher numbers stay;
        - the call history (``AcsApiCall``) that names those orders or
          their vouchers (children included), by column or, for a voucher,
          anywhere in the request or answer: deleted, as the diagnostics it
          is. A COD or pickup-list answer lists many parcels and has no
          order column, so the voucher in its content is what finds it;
        - what ACS sent back: the tracking checkpoints' notes and the
          receiver a COD payout names, in their columns and the raw rows
          they came in. The checkpoints, the payout amounts and the
          payout's ``pod`` (the voucher number ACS reported) stay.

        Run again for orders already erased (``order.privacy``), it clears
        what arrived since.
        """
        from django.db.models import Q, TextField
        from django.db.models.functions import Cast

        from core.utils.json_paths import drop_paths
        from shipping_acs.models import (
            AcsApiCall,
            AcsCodPayout,
            AcsShipment,
            AcsTrackingEvent,
        )

        # A tracking or COD row names the voucher, not the order.
        vouchers: set[str] = set()
        scrubbed = 0
        for shipment in AcsShipment.objects.filter(order_id__in=order_ids):
            metadata = shipment.metadata or {}
            children = metadata.get("multipart_vouchers")
            # A shipment not yet minted has no voucher (``None``).
            vouchers.update(
                str(voucher)
                for voucher in (
                    shipment.voucher_no,
                    *(children if isinstance(children, list) else []),
                )
                if voucher
            )
            if drop_paths(metadata, AcsShipment.PERSONAL_METADATA):
                shipment.metadata = metadata
                shipment.save(update_fields=["metadata"])
                scrubbed += 1

        # A voucher number is the order's own, exact and unique; a name or
        # a phone is not (two customers can share one), so the content is
        # searched for the vouchers alone, as whole numbers.
        named = Q(order_id__in=order_ids) | Q(voucher_no__in=vouchers)
        for voucher in vouchers:
            token = rf"(^|[^0-9]){re.escape(voucher)}([^0-9]|$)"
            named |= Q(request_text__regex=token)
            named |= Q(response_text__regex=token)
        calls = (
            AcsApiCall.objects.annotate(
                request_text=Cast("request_params", TextField()),
                response_text=Cast("response", TextField()),
            )
            .filter(named)
            .values_list("pk", flat=True)
        )
        deleted = AcsApiCall.objects.filter(pk__in=list(calls)).delete()[0]

        events = 0
        for event in AcsTrackingEvent.objects.filter(
            shipment__order_id__in=order_ids
        ):
            raw = (
                event.raw_payload if isinstance(event.raw_payload, dict) else {}
            )
            dropped = drop_paths(raw, (("checkpoint_notes",),))
            if event.notes or dropped:
                event.notes = ""
                event.raw_payload = raw
                event.save(update_fields=["notes", "raw_payload"])
                events += 1
        for payout in AcsCodPayout.objects.filter(
            Q(shipment__order_id__in=order_ids) | Q(voucher_no__in=vouchers)
        ):
            raw = (
                payout.raw_payload
                if isinstance(payout.raw_payload, dict)
                else {}
            )
            dropped = drop_paths(raw, (("Parcel_Receiver",),))
            if payout.parcel_receiver or dropped:
                payout.parcel_receiver = ""
                payout.raw_payload = raw
                payout.save(update_fields=["parcel_receiver", "raw_payload"])
                events += 1

        return {
            "carrier_pii_scrubbed": scrubbed,
            "acs_api_calls": deleted,
            "carrier_events_scrubbed": events,
        }

    def orders_in_motion(self, order_ids: list[int]) -> set[int]:
        """Orders whose ACS shipment is not in a terminal state (the poll
        still reads it), or was delivered with cash to collect and no
        payout yet (the COD answer still names its receiver)."""
        from django.db.models import Q

        from shipping_acs.enum.shipment_state import (
            TERMINAL_STATES,
            AcsShipmentState,
        )
        from shipping_acs.models import AcsShipment

        return set(
            AcsShipment.objects.filter(order_id__in=order_ids)
            .filter(
                ~Q(shipment_state__in=TERMINAL_STATES)
                | Q(
                    shipment_state=AcsShipmentState.DELIVERED,
                    cod_amount__gt=0,
                    cod_payouts__isnull=True,
                    cod_received_outside_acs_at__isnull=True,
                )
            )
            .values_list("order_id", flat=True)
        )

    def recipient_data(self, order_ids: list[int]) -> dict[int, dict]:
        """What ACS reported about the recipient of each order, for a data
        export: the tracking checkpoints' notes and the receiver a COD
        payout names (what :meth:`erase_recipient_data` clears)."""
        from shipping_acs.models import AcsCodPayout, AcsTrackingEvent

        data: dict[int, dict] = {}
        for order_id, notes in (
            AcsTrackingEvent.objects.filter(shipment__order_id__in=order_ids)
            .exclude(notes="")
            .order_by("event_time")
            .values_list("shipment__order_id", "notes")
        ):
            entry = data.setdefault(order_id, {})
            entry.setdefault("acs_tracking_notes", []).append(notes)
        for order_id, receiver in (
            AcsCodPayout.objects.filter(shipment__order_id__in=order_ids)
            .exclude(parcel_receiver="")
            .values_list("shipment__order_id", "parcel_receiver")
        ):
            entry = data.setdefault(order_id, {})
            entry.setdefault("acs_cod_receivers", []).append(receiver)
        return data

    def serialize_shipment(
        self, shipment: Any, *, context: Mapping[str, Any]
    ) -> dict | None:
        from shipping_acs.serializers.shipment import (
            AcsShipmentDetailSerializer,
        )

        return AcsShipmentDetailSerializer(shipment, context=context).data

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        from django.utils.translation import gettext_lazy as _

        if kind != ShippingKind.PICKUP_POINT:
            return {}
        if not payload.get("acs_station_external_id"):
            error: str = str(
                _(
                    "Select an ACS Smartpoint locker before placing an "
                    "order with locker delivery."
                )
            )
            return {"acs_station_external_id": [error]}
        return self.pickup_point_errors(
            "acs_station_external_id",
            {
                "acs_station_external_id": payload["acs_station_external_id"],
                "acs_station_branch": payload.get("acs_station_branch") or "",
            },
            country_code=payload.get("country_id") or "",
        )

    # ------------------------------------------------------------------
    # Per-kind feature gating
    # ------------------------------------------------------------------

    def supported_settlements(
        self, kind: ShippingKind
    ) -> frozenset[PaySettlement] | None:
        """ACS collects at the door or its counter — never at a locker.

        The courier carries cash and a POS, so ``COURIER_CASH`` is its
        native collect-on-delivery product for both kinds. What ACS
        cannot do is ``CARRIER_TERMINAL``: that is BoxNow's locker
        machine, and putting such a pay-way on an ACS voucher would ask
        a courier to collect money the shopper is meant to pay at a
        different company's hardware.

        The exclusion is the point of declaring this at all. Both
        products used to look identical through
        ``PayWay.is_cash_on_delivery``, so nothing stopped them
        crossing carriers.
        """
        return frozenset(
            {
                PaySettlement.ONLINE,
                PaySettlement.COURIER_CASH,
                PaySettlement.OFFLINE_TRANSFER,
            }
        )

    def is_kind_enabled(
        self, kind: ShippingKind, *, country_code: str | None
    ) -> bool:
        """Gate ACS availability on tenant credentials and, for lockers,
        on the stations the picker would list.

        An unconfigured tenant (no ``Tenant.acs_*`` credentials) gets
        NEITHER kind — ACS is entirely unavailable, not just missing
        Smartpoint. Beyond that:

        Home delivery rides on ``ShippingProvider.is_active`` only —
        the same master switch ops use to enable ACS overall.
        Locker pickup also needs the nightly ``sync_acs_stations`` to
        have filled the cache: the kind is enabled only while an active
        ``AcsStation`` of a locker kind exists for ``country_code``, the
        very rows the station picker lists, so checkout never offers a
        picker with nothing in it.

        ``country_code=None`` means "any country" (the pay-way filter
        has no destination yet): enabled when a locker station exists in
        any country. That keeps ACS a candidate whenever it could serve
        the kind somewhere, so its pay-way rules join the intersection
        instead of being dropped, and a pay way ACS would refuse is never
        offered on the strength of not knowing the country.
        """
        from shipping_acs import config as acs_config

        if not acs_config.is_configured():
            return False
        if kind != ShippingKind.PICKUP_POINT:
            return True

        from shipping_acs.models import AcsStation

        stations = AcsStation.objects.filter(is_active=True)
        if country_code is None:
            stations = stations.filter(
                shop_kind__in=acs_config.all_locker_kinds()
            )
        else:
            country_code = country_code.upper()
            stations = stations.filter(
                country_code=country_code,
                shop_kind__in=acs_config.shop_kinds_for_country(country_code),
            )
        return stations.exists()

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
        """Persist the ACS shipment row attached to ``order``.

        Mirrors what :meth:`OrderService._create_acs_shipment_row` did
        before the abstraction landed: pulls the locker IDs and
        charge type out of the checkout payload and computes the
        item quantity / weight from the cart line items.

        ``Charge_Type`` is **always** COD on the ACS voucher because our
        commercial contract does not enable PREPAID (Charge_Type=1) —
        ACS rejects PREPAID calls with "Μη αποδεκτή τιμή χρέωσης
        μεταφορικών". What the courier collects at the door depends on
        the pay-way: COD pay-ways sync ``cod_amount`` to the order
        total at mint time (see ``AcsService.create_voucher_for_order``);
        online pay-ways (Viva, Stripe) leave it at 0 so the voucher
        mints but nothing is collected on delivery.

        The explicit ``acs_charge_type`` payload key still wins so an
        admin can force a per-order override if the contract ever gets
        PREPAID enabled.

        Idempotent — re-runs are no-ops because the order already has
        a row at that point.
        """
        from shipping.utils import compute_total_weight_grams
        from shipping_acs.enum.charge_type import AcsChargeType
        from shipping_acs.enum.cod_payment_way import AcsCodPaymentWay
        from shipping_acs.models import AcsShipment, AcsStation

        if AcsShipment.objects.filter(order=order).exists():
            return

        external_id = payload.get("acs_station_external_id", "") or ""
        branch_code = payload.get("acs_station_branch", "") or ""
        delivery_kind = order.shipping_kind or kind.value

        pay_way = getattr(order, "pay_way", None)
        # ACS collects at the door, so ``COURIER_CASH`` is its COD case
        # — and only its own. A ``CARRIER_TERMINAL`` pay-way (BoxNow PAY
        # ON THE GO) is settled at a locker machine and must never put
        # a COD amount on an ACS voucher; ``supported_settlements``
        # keeps it out of an ACS checkout in the first place.
        is_cod = bool(
            pay_way
            and PaySettlement(pay_way.settlement) == PaySettlement.COURIER_CASH
        )
        # Default to COD because the contract is COD-only; admins can
        # still override per order via the ``acs_charge_type`` payload
        # key. ``Acs_Delivery_Products="COD"`` only goes on the voucher
        # for real COD pay-ways — online-paid orders get a COD voucher
        # with Cod_Ammount=0 but should not carry the COD product flag.
        charge_type = int(payload.get("acs_charge_type") or AcsChargeType.COD)
        cod_payment_way = (
            AcsCodPaymentWay.CASH if charge_type == AcsChargeType.COD else None
        )
        delivery_products = "COD" if is_cod else ""

        weight_grams = 0
        if items is not None:
            weight_grams = compute_total_weight_grams(list(items))

        # ACS ``Item_Quantity`` is the number of **physical parcels**
        # in the shipment — NOT the number of cart line items. For a
        # shop that bundles every order into one box (the default
        # ours operates under), this is always ``1``. When we sent
        # ``sum(cart quantities)`` instead, ACS interpreted it as a
        # multi-parcel shipment and ``get_multipart_vouchers`` minted
        # one child voucher per "piece" (parent + N-1 children) —
        # see order #56, which produced 3 vouchers for a 3-item cart.
        #
        # ACS PDF p.8 also explicitly forbids ``Item_Quantity > 1``
        # for Smartpoint pickup, so a single hard default of 1 is
        # safe everywhere; the Smartpoint clamp the previous version
        # carried is now redundant.
        #
        # Admins who genuinely ship in multiple parcels can pass
        # ``acs_item_quantity`` in the order payload to override
        # per-order — same admin-override hatch we use for
        # ``acs_charge_type``. Lesson from the PREPAID-leak incident:
        # the serializer field for that override MUST NOT carry a
        # default (any default leaks past this fallback because
        # truthy values short-circuit ``or``).
        raw_qty = payload.get("acs_item_quantity")
        try:
            item_quantity = int(raw_qty) if raw_qty is not None else 1
        except TypeError, ValueError:
            item_quantity = 1
        item_quantity = max(item_quantity, 1)

        station = None
        if external_id:
            # external_id is the AREA station code shared by every
            # locker in that area — the (external_id, branch_code)
            # pair identifies the actual locker the customer picked.
            # Fall back to code-only matching for payloads without a
            # branch (physical-shop pickups, stale clients); the
            # voucher itself always uses the raw payload values, so
            # this FK is informational either way.
            station = (
                AcsStation.objects.filter(
                    external_id=external_id, branch_code=branch_code
                ).first()
                or AcsStation.objects.filter(external_id=external_id).first()
            )

        AcsShipment.objects.create(
            order=order,
            station_destination=station,
            station_destination_external_id=external_id,
            station_branch_destination=branch_code,
            delivery_kind=delivery_kind,
            charge_type=charge_type,
            cod_payment_way=cod_payment_way,
            delivery_products=delivery_products,
            weight_grams=weight_grams,
            item_quantity=item_quantity,
        )

    def dispatch_create_shipment_task(
        self, order: Order, *, schema_name: str | None = None
    ) -> None:
        """Enqueue the ACS create-voucher Celery task for ``order``."""
        from shipping_acs.tasks import create_acs_voucher_for_order

        logger.info(
            "ACS dispatch: queued voucher mint for order=%s",
            order.id,
            extra={"order_id": order.id, "carrier": "acs"},
        )
        # Stamp the caller-captured tenant schema so the worker binds
        # the right store even when this dispatch is deferred past the
        # request's schema context (see the base method's contract). A
        # broker failure is kept as a ``DeferredDispatch`` instead of
        # leaving a paid order with no voucher.
        from tenant.celery import dispatch_on_commit

        dispatch_on_commit(
            create_acs_voucher_for_order, [order.id], schema_name=schema_name
        )

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

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
        """Optional live quote via ``ACS_Price_Calculation``.

        ``rate.price`` (was ``ACS_SHIPPING_PRICE``) remains the source
        of truth when ``ACS_DYNAMIC_PRICING_ENABLED`` is off OR when
        the live API call fails — a transient ACS outage must never
        block checkout.

        ``cached_only`` never calls ACS: it answers from the cache (a
        price, or the remembered "no live price"), and raises
        ``ShippingLivePriceUnresolvedError`` when the cache holds neither.
        """
        from core.store_settings import store_setting

        if not bool(store_setting("ACS_DYNAMIC_PRICING_ENABLED")):
            return None

        quote = self._fetch_live_quote(
            country_id=country_code,
            region_id=region_id,
            currency=currency,
            weight_grams=weight_grams,
            cached_only=cached_only,
        )
        if quote is None:
            return None
        amount, _currency = quote
        return Decimal(str(amount))

    # How long a live price is trusted, and how long "ACS gave none"
    # is: a failed call is remembered briefly, so neither a polled
    # quote nor every order during an ACS outage asks again, and order
    # creation (which reads the cache alone) can tell "ACS gave no price,
    # charge the stored rate" from "nobody asked yet".
    _QUOTE_TTL_SECONDS: ClassVar[int] = 300
    _NO_QUOTE_TTL_SECONDS: ClassVar[int] = 60
    _NO_QUOTE: ClassVar[str] = "none"
    # The version is in the key because two releases read one Redis
    # while a deploy rolls. v2 is the first to store ``_NO_QUOTE``; the
    # release before it calls ``float()`` on whatever it finds under
    # the unversioned key, so the marker there would fail every
    # ACS-priced request it served until the entry expired. A release
    # that changes what an entry holds takes the next version.
    _QUOTE_KEY_PREFIX: ClassVar[str] = "acs:price_quote:v2"

    # ACS minimum chargeable weight per published tariff. Anything
    # below 500g is billed at 500g.
    _MIN_CHARGEABLE_GRAMS: ClassVar[int] = 500

    @classmethod
    def _bucket_weight_grams(cls, weight_grams: int | None) -> int:
        """Bucket cart weight to the ACS billing brackets so the cache
        key collapses ``487g`` and ``499g`` to the same quote without
        bombarding the ACS API. The brackets mirror the published
        tariff steps: 0.5, 1, 2, 3, 4, 5, 6 kg, then 1 kg increments.
        """
        if weight_grams is None or weight_grams <= 0:
            return cls._MIN_CHARGEABLE_GRAMS
        if weight_grams <= 500:
            return 500
        if weight_grams <= 1000:
            return 1000
        if weight_grams <= 2000:
            return 2000
        # 1 kg buckets up to 6 kg.
        if weight_grams <= 6000:
            return ((weight_grams + 999) // 1000) * 1000
        # > 6 kg: round up to next kg.
        return ((weight_grams + 999) // 1000) * 1000

    @classmethod
    def _fetch_live_quote(
        cls,
        *,
        country_id: str | None,
        region_id: str | None,
        currency: str,
        weight_grams: int | None = None,
        cached_only: bool = False,
    ) -> tuple[float, str] | None:
        """Call ACS_Price_Calculation with caching + graceful failure.

        The answer is cached either way: a price for
        ``_QUOTE_TTL_SECONDS``, a failure (``None``: the stored rate is
        charged) for ``_NO_QUOTE_TTL_SECONDS``. ``cached_only`` reads
        that cache and never calls ACS; with no entry it raises
        ``ShippingLivePriceUnresolvedError``.

        Uses ``weight_grams`` bucketed to the ACS billing brackets so
        the customer-shown quote matches what the voucher API will
        actually charge. Falls back to the 500g floor when the cart
        weight isn't known (e.g. quote-only call without items).

        Cache key: (country, region, weight bucket). 5-minute TTL so
        an admin tariff change propagates without a cache flush.
        """
        from django.core.cache import cache

        from shipping.exceptions import ShippingLivePriceUnresolvedError
        from shipping_acs import config as acs_config

        # ACS_Price_Calculation requires both Origin and Destination
        # station codes. Empty/missing values return
        # ``Άγνωστο κατάστημα παραλαβής``. The merchant pickup branch
        # is parsed from the billing code (or via metadata override —
        # see ``shipping_acs/config.py::station_origin``). When we
        # don't have a destination yet (sidebar quote pre-address)
        # we use origin for both — ACS prices intra-region same as
        # the merchant's home region (~+0.20€ delta to inter-region),
        # well under the flat-rate baseline.
        origin = acs_config.station_origin()
        if not origin:
            logger.warning(
                "ACS_STATION_ORIGIN not configured — falling back to "
                "flat rate. Set ShippingProvider(code='acs').metadata"
                "['station_origin'] in admin or check the format of "
                "ACS_BILLING_CODE."
            )
            return None
        destination = origin

        bucketed_grams = cls._bucket_weight_grams(weight_grams)
        cache_key = (
            f"{cls._QUOTE_KEY_PREFIX}:{country_id or '-'}:{region_id or '-'}"
            f":{bucketed_grams}:{origin}:{destination}"
        )
        cached = cache.get(cache_key)
        if cached == cls._NO_QUOTE:
            return None
        if cached is not None:
            return (float(cached), currency)
        if cached_only:
            raise ShippingLivePriceUnresolvedError

        amount = cls._ask_live_quote(origin, destination, bucketed_grams)
        if amount is None:
            cache.set(
                cache_key, cls._NO_QUOTE, timeout=cls._NO_QUOTE_TTL_SECONDS
            )
            return None
        cache.set(cache_key, amount, timeout=cls._QUOTE_TTL_SECONDS)
        return (amount, currency)

    @classmethod
    def _ask_live_quote(
        cls, origin: str, destination: str, bucketed_grams: int
    ) -> float | None:
        """One ``ACS_Price_Calculation`` call; ``None`` for any failure
        (logged), which the caller answers with the stored rate."""
        from django.utils import timezone

        from shipping_acs.client import AcsClient
        from shipping_acs.exceptions import AcsAPIError, AcsConfigError
        from shipping_acs.services import _kg_from_grams

        try:
            client = AcsClient()
            response = client.price_calculation(
                {
                    "Billing_Category": 2,
                    "Acs_Station_Origin": origin,
                    "Acs_Station_Destination": destination,
                    # ACS reads numeric strings with the Greek locale
                    # (comma-decimal). ``_kg_from_grams`` returns the
                    # already-formatted ``"0,5"`` / ``"2,5"`` string —
                    # use the same helper that the voucher mint uses
                    # so the quote and the charge match exactly.
                    "Weight": _kg_from_grams(bucketed_grams),
                    "Charge_Type": 2,
                    "Pickup_Date": timezone.localdate().isoformat(),
                }
            )
        except (AcsConfigError, AcsAPIError) as exc:
            logger.warning(
                "ACS_Price_Calculation failed (%s) — falling back to "
                "flat rate.",
                exc,
            )
            return None
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "Unexpected error during ACS price quote: %s — "
                "falling back to flat rate.",
                exc,
            )
            return None

        # ACS returns a 200 with ``Error_Message`` populated for
        # business errors (e.g. unknown station). Surface those in
        # logs so an operator can correct the metadata, then fall
        # back to the flat rate.
        error_message = response.get("Error_Message") if response else None
        if error_message:
            # Don't log the station codes themselves — they derive from
            # ACS_BILLING_CODE (CodeQL py/clear-text-logging-sensitive-
            # data); the operator can read them from the provider
            # metadata / billing code directly.
            logger.warning(
                "ACS_Price_Calculation business error: %s — falling "
                "back to flat rate. Check ShippingProvider metadata "
                "['station_origin'] / ACS_BILLING_CODE.",
                error_message,
            )
            return None

        amount_raw = response.get("Total_Ammount") or response.get(
            "Basic_Ammount"
        )
        if amount_raw is None:
            return None
        # ACS responses follow the same Greek locale as their request
        # body: comma-decimal. ``float("47,01")`` raises ValueError so
        # without the swap every live quote silently falls back to the
        # flat rate.
        normalised = (
            amount_raw.replace(",", ".")
            if isinstance(amount_raw, str)
            else amount_raw
        )
        try:
            amount = float(normalised)
        except TypeError, ValueError:
            logger.warning(
                "Could not parse ACS price quote amount %r — "
                "falling back to flat rate.",
                amount_raw,
            )
            return None
        return amount
