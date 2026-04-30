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

        # BoxNow does not support cash-on-delivery for standard
        # partners — the API rejects with P411 ("not eligible to use
        # COD") asynchronously inside the Celery task. Fail fast at
        # the create-order boundary while we still have a customer
        # in the checkout to surface a useful message to. The
        # ``_pay_way_is_online`` key is injected by ``OrderService``
        # at the call site so this validator stays a pure function
        # of its inputs.
        pay_way_is_online = payload.get("_pay_way_is_online")
        if pay_way_is_online is False:
            errors["pay_way"] = [
                str(
                    _(
                        "BOX NOW locker delivery does not support cash on "
                        "delivery — choose an online payment method."
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

        BoxNowShipment.objects.create(
            order=order,
            locker_external_id=locker_id,
            locker=locker,
            compartment_size=compartment_size,
            weight_grams=weight_grams,
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
