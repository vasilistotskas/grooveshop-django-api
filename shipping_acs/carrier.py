"""ACS adapter for the generic shipping abstraction."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from shipping.enum import ShippingKind
from shipping.interfaces import ShippingCarrierInterface, register_provider

if TYPE_CHECKING:
    from order.models.order import Order

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

    def fetch_label_bytes(self, shipment: Any) -> bytes:
        from shipping_acs.services import AcsService

        return AcsService.fetch_label_bytes(shipment)

    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        # Polling-only — caller dispatches via the Celery task.
        return []

    def shipment_for_order(self, order: Order):
        return getattr(order, "acs_shipment", None)

    def serialize_shipment(
        self, shipment: Any, *, context: dict
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
        return {}

    # ------------------------------------------------------------------
    # Per-kind feature gating
    # ------------------------------------------------------------------

    def is_kind_enabled(self, kind: ShippingKind) -> bool:
        """Gate the Smartpoint locker option behind ACS_SMARTPOINT_ENABLED.

        Home delivery rides on ``ShippingProvider.is_active`` only —
        the same master switch ops use to enable ACS overall.
        Smartpoint locker pickup adds a separate Setting toggle so
        operations can disable just the locker UX (e.g. while the
        AcsStation cache is being seeded for the first time) without
        also taking home delivery offline.
        """
        if kind == ShippingKind.PICKUP_POINT:
            from extra_settings.models import Setting

            return bool(Setting.get("ACS_SMARTPOINT_ENABLED", default=False))
        return True

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

        try:
            from shipping_acs.models import AcsShipment, AcsStation
        except ImportError:
            logger.warning(
                "shipping_acs app not available — skipping ACS shipment "
                "creation for order %s",
                order.id,
            )
            return

        if AcsShipment.objects.filter(order=order).exists():
            return

        external_id = payload.get("acs_station_external_id", "") or ""
        branch_code = payload.get("acs_station_branch", "") or ""
        delivery_kind = order.shipping_kind or kind.value

        pay_way = getattr(order, "pay_way", None)
        is_cod = bool(pay_way and pay_way.is_cash_on_delivery)
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
        except (TypeError, ValueError):
            item_quantity = 1
        if item_quantity < 1:
            item_quantity = 1

        station = None
        if external_id:
            station = AcsStation.objects.filter(external_id=external_id).first()

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

    def dispatch_create_shipment_task(self, order: Order) -> None:
        """Enqueue the ACS create-voucher Celery task for ``order``."""
        try:
            from shipping_acs.tasks import create_acs_voucher_for_order
        except ImportError:
            logger.warning(
                "shipping_acs app not available — skipping ACS task "
                "dispatch for order %s",
                order.id,
            )
            return

        logger.info(
            "ACS dispatch: queued voucher mint for order=%s",
            order.id,
            extra={"order_id": order.id, "carrier": "acs"},
        )
        create_acs_voucher_for_order.delay(order.id)

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    def calculate_shipping_cost(
        self,
        *,
        order_value_amount: float,
        currency: str,
        kind: ShippingKind,
        country_id: str | None = None,
        region_id: str | None = None,
        weight_grams: int | None = None,
    ) -> tuple[float, str] | None:
        from extra_settings.models import Setting

        free_threshold = float(
            Setting.get("ACS_FREE_SHIPPING_THRESHOLD", default=40.00)
        )
        if order_value_amount >= free_threshold:
            return (0.0, currency)

        # Phase 4a: optional live quote via ACS_Price_Calculation.
        # ACS_SHIPPING_PRICE Setting remains the source of truth when
        # the toggle is off OR when the live API call fails — a
        # transient ACS outage must never block checkout.
        if bool(Setting.get("ACS_DYNAMIC_PRICING_ENABLED", default=False)):
            quote = self._fetch_live_quote(
                country_id=country_id,
                region_id=region_id,
                currency=currency,
                weight_grams=weight_grams,
            )
            if quote is not None:
                return quote

        base = float(Setting.get("ACS_SHIPPING_PRICE", default=3.50))
        return (base, currency)

    def free_shipping_threshold(
        self,
        kind: ShippingKind,
    ) -> Decimal | None:
        from extra_settings.models import Setting

        # ACS applies the same threshold to both home delivery and
        # Smartpoint pickup (see :meth:`calculate_shipping_cost`).
        raw = Setting.get("ACS_FREE_SHIPPING_THRESHOLD", default=40.00)
        return Decimal(str(raw))

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
    ) -> tuple[float, str] | None:
        """Call ACS_Price_Calculation with caching + graceful failure.

        Uses ``weight_grams`` bucketed to the ACS billing brackets so
        the customer-shown quote matches what the voucher API will
        actually charge. Falls back to the 500g floor when the cart
        weight isn't known (e.g. quote-only call without items).

        Cache key: (country, region, weight bucket). 5-minute TTL so
        an admin tariff change propagates without a cache flush.
        """
        from django.core.cache import cache
        from django.utils import timezone

        from shipping_acs import config as acs_config
        from shipping_acs.client import AcsClient
        from shipping_acs.exceptions import AcsAPIError, AcsConfigError
        from shipping_acs.services import _kg_from_grams

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
            f"acs:price_quote:{country_id or '-'}:{region_id or '-'}"
            f":{bucketed_grams}:{origin}:{destination}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return (float(cached), currency)

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
            logger.warning(
                "ACS_Price_Calculation business error (origin=%s "
                "dest=%s): %s — falling back to flat rate.",
                origin,
                destination,
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
        except (TypeError, ValueError):
            logger.warning(
                "Could not parse ACS price quote amount %r — "
                "falling back to flat rate.",
                amount_raw,
            )
            return None

        cache.set(cache_key, amount, timeout=300)
        return (amount, currency)
