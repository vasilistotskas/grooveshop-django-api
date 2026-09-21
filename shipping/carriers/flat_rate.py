"""The merchant's own delivery, at the store's flat rate.

Every other adapter fronts a courier API and therefore gates itself on
tenant credentials — ACS: "an unconfigured tenant gets NEITHER kind";
BoxNow: "no BoxNow kind at all". That is correct for them, and it left a
hole underneath: a store with no carrier contract has NO shipping option,
so ``/shipping/options`` returns ``[]`` and checkout dead-ends at the
delivery step. Measured on the demo store 2026-09-21 — items could be
added to the cart and the order could never be placed. Every newly
provisioned tenant had the same cliff until it signed with a courier.

This adapter closes it. There is no API behind it: the merchant packs the
parcel and hands it to whoever they like. So it has no credentials to
gate on, no label to fetch and no tracking to poll, and it is registered
by ``shipping`` itself rather than by a carrier app so that it is always
present.

The price is NOT a new setting. ``OrderService`` already had a flat-rate
rule — ``CHECKOUT_SHIPPING_PRICE`` / ``FREE_SHIPPING_THRESHOLD`` — used
as a last-resort fallback when no carrier priced the order. That policy
was invisible at checkout, because the quote only happened at order time.
Reading the same two rows here turns it into a first-class option the
shopper can see and choose, with no second definition of "the store's
shipping price" to drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from pay_way.enum.settlement import PaySettlement
from shipping.enum import ShippingKind
from shipping.interfaces import ShippingCarrierInterface, register_provider

if TYPE_CHECKING:
    from order.models.order import Order

#: Matches ``OrderService._shipping_cost``'s own fallback, so a store
#: that has never touched its settings is priced identically whether the
#: quote comes from here or from that path.
DEFAULT_PRICE = Decimal("3.00")
DEFAULT_FREE_THRESHOLD = Decimal("50.00")


def _setting(name: str, default: Decimal) -> Decimal:
    """Read a money Setting as ``Decimal``, falling back to ``default``.

    Via ``str`` rather than ``float``: these rows are authored as
    decimal strings in the admin and binary floating point would put
    3.00 on the invoice as 2.9999999999999996.
    """
    from extra_settings.models import Setting

    raw = Setting.get(name, default=None)
    if raw in (None, ""):
        return default
    try:
        return Decimal(str(raw))
    except ArithmeticError, ValueError:
        return default


@register_provider
class FlatRateCarrier(ShippingCarrierInterface):
    code: ClassVar[str] = "flat_rate"

    #: Nothing to pick — no locker, no pickup point, no per-order
    #: carrier data. The interface anticipates exactly this shape.
    payload_keys: ClassVar[tuple[str, ...]] = ()

    # ------------------------------------------------------------------
    # Pricing — the only part of this adapter that does real work
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
        """The store's flat rate, free above its own threshold.

        Weight and destination are deliberately ignored: a merchant
        quoting a single number to their customers is the whole point of
        this carrier. A store that needs weight bands or zone pricing
        wants a real courier integration, not this.
        """
        if kind != ShippingKind.HOME_DELIVERY:
            return None

        threshold = _setting("FREE_SHIPPING_THRESHOLD", DEFAULT_FREE_THRESHOLD)
        if Decimal(str(order_value_amount)) >= threshold:
            return 0.0, currency

        price = _setting("CHECKOUT_SHIPPING_PRICE", DEFAULT_PRICE)
        return float(price), currency

    def free_shipping_threshold(self, kind: ShippingKind) -> Decimal | None:
        """Advertise the same threshold the quote above honours.

        Without this the PDP and cart would show no "free over X"
        line for a store whose only carrier is this one — the view
        skips ``None`` — while checkout went on applying the threshold.
        """
        if kind != ShippingKind.HOME_DELIVERY:
            return None
        return _setting("FREE_SHIPPING_THRESHOLD", DEFAULT_FREE_THRESHOLD)

    # ------------------------------------------------------------------
    # Lifecycle — there is no carrier system to talk to
    # ------------------------------------------------------------------
    #
    # These are not stubs awaiting an implementation: a merchant-
    # fulfilled parcel genuinely has no remote shipment, no label
    # endpoint and no tracking feed. The interface exists so callers can
    # dispatch unconditionally, and the honest answer here is "nothing".

    def create_shipment(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return None

    def cancel_shipment(self, shipment: Any, *, reason: str = "") -> None:
        return None

    def fetch_label_bytes(self, shipment: Any) -> bytes:
        """No label exists — the merchant writes their own.

        Raised rather than returned empty so a caller that reaches here
        finds out, instead of handing the operator a zero-byte PDF.
        """
        raise NotImplementedError(
            "flat_rate is merchant-fulfilled and has no carrier label"
        )

    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        return []

    def shipment_for_order(self, order: Order) -> Any | None:
        return None

    def serialize_shipment(
        self, shipment: Any, *, context: Mapping[str, Any]
    ) -> dict | None:
        return None

    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        return {}

    def supported_settlements(
        self, kind: ShippingKind
    ) -> frozenset[PaySettlement] | None:
        """Everything except the one a carrier system performs.

        The default ``None`` — no constraint — was wrong, and checkout
        showed it: BOX NOW PAY ON THE GO appeared against flat-rate HOME
        DELIVERY, offering to let a carrier collect for a parcel that
        carrier never touches. ``CARRIER_TERMINAL`` is not a terminal
        and not cash; it is the carrier collecting online after dispatch
        by sending its own payment link, which only exists if there is a
        carrier. There is not one here.

        The rest this carrier can genuinely do: the merchant is paid at
        checkout, or by transfer, or takes cash at the door — that last
        one is the whole reason a locker cannot do it and this can.
        """
        return frozenset(
            {
                PaySettlement.ONLINE,
                PaySettlement.COURIER_CASH,
                PaySettlement.OFFLINE_TRANSFER,
            }
        )
