"""BoxNow adapter for the generic shipping abstraction.

Registers under code ``"boxnow"`` at ``ShippingBoxNowConfig.ready()``
time.  All public methods delegate to the existing ``BoxNowService``
class so behaviour is unchanged from the pre-abstraction code path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from shipping.enum import ShippingKind
from shipping.interfaces import ShippingCarrierInterface, register_provider

if TYPE_CHECKING:
    from order.models.order import Order

    from shipping_boxnow.models import BoxNowShipment

logger = logging.getLogger(__name__)


@register_provider
class BoxNowCarrier(ShippingCarrierInterface):
    code: ClassVar[str] = "boxnow"

    # Carrier-specific request-body keys popped before Order.create().
    payload_keys: ClassVar[tuple[str, ...]] = (
        "boxnow_locker_id",
        "boxnow_compartment_size",
    )

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

    def fetch_label_bytes(self, shipment: Any) -> bytes:
        from shipping_boxnow.services import BoxNowService

        return BoxNowService.fetch_label_bytes(shipment)

    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        # BoxNow is webhook-driven — events arrive via the webhook
        # receiver, not by polling. No-op here.
        return []

    def shipment_for_order(self, order: Order):
        return getattr(order, "boxnow_shipment", None)

    def serialize_shipment(
        self, shipment: Any, *, context: dict
    ) -> dict | None:
        from shipping_boxnow.serializers.shipment import (
            BoxNowShipmentDetailSerializer,
        )

        return BoxNowShipmentDetailSerializer(shipment, context=context).data

    # ------------------------------------------------------------------
    # Validation (called by order/services._validate_address_data)
    # ------------------------------------------------------------------

    def filter_pay_ways(self, queryset, *, kind: ShippingKind):
        """BoxNow supports prepaid AND COD on lockers.

        The legacy "BoxNow rejects COD on locker pickup" rule was
        true before BoxNow shipped their PAY ON THE GO product
        (2025-Q3), which collects cash / card at the locker on
        delivery. Once BoxNow activates PAY ON THE GO on the
        partner account, an offline pay-way (Αντικαταβολή) maps to
        ``paymentMode='cod'`` + ``amountToBeCollected=<total>`` in
        the create-shipment payload (handled in
        ``create_shipment_row`` below) and BoxNow honours it.

        We therefore expose ALL active pay-ways for both
        ``HOME_DELIVERY`` and ``PICKUP_POINT``. Partners that don't
        have PAY ON THE GO active should remove the offline pay-way
        from their PayWay catalogue (or mark it inactive) so it
        doesn't appear in the picker; that's a deployment-config
        decision, not a courier-rule one.
        """
        return queryset

    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        from django.utils.translation import gettext_lazy as _

        if kind != ShippingKind.PICKUP_POINT:
            return {}

        errors: dict[str, list[str]] = {}

        if not payload.get("boxnow_locker_id"):
            errors["boxnow_locker_id"] = [
                str(
                    _(
                        "Select a BOX NOW locker before placing an order "
                        "with locker delivery."
                    )
                )
            ]

        return errors

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
        of the three ``OrderService.create_order*`` paths.  Idempotent
        — re-runs are no-ops because the row already exists.

        Reads:
        * ``boxnow_locker_id`` (string)
        * ``boxnow_compartment_size`` (1, 2, 3)

        Computes the parcel weight from the cart line items so the
        BoxNow voucher PDF prints the real weight (BoxNow tariffs by
        weight bracket).
        """
        from order.services import _compute_total_weight_grams

        try:
            from shipping_boxnow.models import BoxNowLocker, BoxNowShipment
        except ImportError:
            logger.warning(
                "shipping_boxnow app not available — skipping BoxNow "
                "shipment creation for order %s",
                order.id,
            )
            return

        if kind != ShippingKind.PICKUP_POINT:
            return
        if BoxNowShipment.objects.filter(order=order).exists():
            return

        locker_id = payload.get("boxnow_locker_id", "") or ""
        compartment_size = int(payload.get("boxnow_compartment_size", 1) or 1)

        weight_grams = 0
        if items is not None:
            weight_grams = _compute_total_weight_grams(items)

        locker = (
            BoxNowLocker.objects.filter(external_id=locker_id).first()
            if locker_id
            else None
        )

        # Derive paymentMode from the order's pay-way.
        # ``PayWay.is_cash_on_delivery`` is the canonical discriminator
        # (see ``pay_way/models.py``): only true cash-on-delivery maps to
        # BoxNow PAY ON THE GO COD. Bank-transfer-style offline pay-ways
        # (``requires_confirmation=True``) are settled off-platform and
        # must ship as PREPAID — otherwise BoxNow would double-collect at
        # the locker.
        #
        # ``amountToBeCollected`` is set lazily in
        # ``BoxNowService.create_shipment_for_order`` (Phase 1) once the
        # order's items + shipping_price + fees are fully persisted —
        # ``order.total_price`` is 0 here because items are saved AFTER
        # this row.
        from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode

        pay_way = getattr(order, "pay_way", None)
        is_cod = bool(pay_way and pay_way.is_cash_on_delivery)
        payment_mode = (
            BoxNowPaymentMode.COD if is_cod else BoxNowPaymentMode.PREPAID
        )

        BoxNowShipment.objects.create(
            order=order,
            locker_external_id=locker_id,
            locker=locker,
            compartment_size=compartment_size,
            weight_grams=weight_grams,
            payment_mode=payment_mode,
        )

    def dispatch_create_shipment_task(self, order: Order) -> None:
        """Enqueue the BoxNow create-shipment Celery task for ``order``."""
        try:
            from shipping_boxnow.tasks import (
                create_boxnow_shipment_for_order,
            )
        except ImportError:
            logger.warning(
                "shipping_boxnow app not available — skipping BoxNow "
                "task dispatch for order %s",
                order.id,
            )
            return

        create_boxnow_shipment_for_order.delay(order.id)

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

        if kind != ShippingKind.PICKUP_POINT:
            return None

        base = float(Setting.get("BOXNOW_SHIPPING_PRICE", default=2.50))
        free_threshold = float(
            Setting.get("BOXNOW_FREE_SHIPPING_THRESHOLD", default=30.00)
        )
        if order_value_amount >= free_threshold:
            return (0.0, currency)
        return (base, currency)
