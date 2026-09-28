"""BoxNow adapter for the generic shipping abstraction.

Registers under code ``"boxnow"`` at ``ShippingBoxNowConfig.ready()``
time.  All public methods delegate to the existing ``BoxNowService``
class so behaviour is unchanged from the pre-abstraction code path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from pay_way.enum.settlement import PaySettlement
from shipping.enum import ShippingKind
from shipping.interfaces import ShippingCarrierInterface, register_provider
from shipping_boxnow.exceptions import BoxNowUnsupportedSettlementError

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

    def is_kind_enabled(self, kind: ShippingKind) -> bool:
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
        """Locker required for a locker order, and it must be in the
        delivery country.

        A locker absent from the local ``BoxNowLocker`` cache is
        ACCEPTED with a warning rather than rejected: the daily sync
        task lags behind BoxNow's own catalogue, so a locker that
        genuinely exists can be momentarily unknown to us — rejecting
        it would block a real order over our own stale cache, not a
        bad selection.
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

        from shipping_boxnow.models import BoxNowLocker

        locker = BoxNowLocker.objects.filter(external_id=locker_id).first()
        if locker is None:
            logger.warning(
                "BoxNow locker %s not found in the local cache — "
                "accepting anyway (daily sync lags BoxNow's own catalogue)",
                locker_id,
                extra={"boxnow_locker_external_id": locker_id},
            )
            return {}

        order_country = (payload.get("country_id") or "").upper()
        if (
            order_country
            and locker.country_code
            and locker.country_code.upper() != order_country
        ):
            return {
                "boxnow_locker_id": [
                    str(
                        _("The selected locker is not in the delivery country.")
                    )
                ]
            }

        return {}

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
        * ``boxnow_compartment_size`` (1, 2, 3)

        Computes the parcel weight from the cart line items so the
        BoxNow voucher PDF prints the real weight (BoxNow tariffs by
        weight bracket).
        """
        from shipping.utils import compute_total_weight_grams
        from shipping_boxnow.models import BoxNowLocker, BoxNowShipment

        if kind != ShippingKind.PICKUP_POINT:
            return
        if BoxNowShipment.objects.filter(order=order).exists():
            return

        locker_id = payload.get("boxnow_locker_id", "") or ""
        compartment_size = int(payload.get("boxnow_compartment_size", 1) or 1)

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
        # method's contract.
        from django.db import connection

        create_boxnow_shipment_for_order.apply_async(
            args=[order.id],
            headers={"_schema_name": schema_name or connection.schema_name},
        )

    # Pricing is a ``ShippingRate`` row now (boxnow/CY/pickup_point,
    # boxnow/GR/pickup_point, ...) — this adapter has no
    # ``live_quote`` override because BoxNow's tariff is contract-flat
    # per partner, not weight- or destination-banded, so the default
    # "no live pricing" behaviour (``rate.price`` unchanged) is correct.
