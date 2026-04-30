"""ACS adapter for the generic shipping abstraction."""

from __future__ import annotations

import logging
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

        ``charge_type`` defaults to PREPAID but is overridden to COD
        when the order's pay_way is an offline (cash-on-delivery)
        provider — that way ACS collects the parcel total from the
        recipient at the door instead of expecting the partner to
        pre-fund the shipment.

        Idempotent — re-runs are no-ops because the order already has
        a row at that point.
        """
        from order.services import _compute_total_weight_grams
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

        # COD detection runs off pay_way.is_online_payment so any
        # offline provider (Αντικαταβολή, Bank Transfer with manual
        # confirmation) is treated as cash-on-delivery for ACS. The
        # explicit ``acs_charge_type`` payload key still wins so an
        # admin override can force PREPAID against an offline pay_way
        # (e.g. for B2B settlements pre-paid on invoice).
        pay_way = getattr(order, "pay_way", None)
        is_offline = bool(pay_way and not pay_way.is_online_payment)
        default_charge = (
            AcsChargeType.COD if is_offline else AcsChargeType.PREPAID
        )
        charge_type = int(payload.get("acs_charge_type") or default_charge)
        cod_payment_way = (
            AcsCodPaymentWay.CASH if charge_type == AcsChargeType.COD else None
        )
        delivery_products = "COD" if charge_type == AcsChargeType.COD else ""

        weight_grams = 0
        item_quantity = 1
        if items is not None:
            items_list = list(items)
            weight_grams = _compute_total_weight_grams(items_list)
            item_quantity = max(sum(int(q or 0) for _, q in items_list) or 1, 1)

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
            )
            if quote is not None:
                return quote

        base = float(Setting.get("ACS_SHIPPING_PRICE", default=3.50))
        return (base, currency)

    @staticmethod
    def _fetch_live_quote(
        *,
        country_id: str | None,
        region_id: str | None,
        currency: str,
    ) -> tuple[float, str] | None:
        """Call ACS_Price_Calculation with caching + graceful failure.

        Uses the ACS minimum chargeable weight (0.5 kg) because
        cart-line items aren't on the request at quote time. Heavier
        orders pay through to the create-voucher API where the actual
        weight is sent — but the customer has already been shown the
        smaller flat-style quote, which is bounded by ACS's published
        tariff for that lane.

        Cache key: country/region tuple. 5-minute TTL so an admin
        tariff change propagates without a cache flush.
        """
        from django.core.cache import cache
        from django.utils import timezone

        from shipping_acs.client import AcsClient
        from shipping_acs.exceptions import AcsAPIError, AcsConfigError

        cache_key = f"acs:price_quote:{country_id or '-'}:{region_id or '-'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return (float(cached), currency)

        try:
            client = AcsClient()
            response = client.price_calculation(
                {
                    "Billing_Category": 2,
                    # Greek locale: ACS reads numeric strings with
                    # comma-decimal (dot is thousands separator). Send
                    # ``"0,5"`` — ``"0.5"`` is parsed as 5 kg and quotes
                    # the 5 kg tariff, inflating the live price shown
                    # to the customer at checkout. Mirrors
                    # ``_kg_from_grams`` in shipping_acs/services.py.
                    "Weight": "0,5",
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

        amount_raw = response.get("Total_Ammount") or response.get(
            "Basic_Ammount"
        )
        if amount_raw is None:
            return None
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            return None

        cache.set(cache_key, amount, timeout=300)
        return (amount, currency)
