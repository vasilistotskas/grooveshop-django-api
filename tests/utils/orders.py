"""Orders whose payment method is part of what a test means.

``OrderFactory`` picks an existing pay way at random. Tests of a courier
voucher mean a cash-on-delivery order: an online pay way with a pending
payment is an unpaid order, which the carriers refuse to ship
(``ShipmentAwaitingPaymentError``). Left to the draw, those tests passed
or failed with the pay way the database happened to return.
"""

from __future__ import annotations

from order.factories.order import OrderFactory
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


def courier_cash_pay_way() -> PayWay:
    """The cash-on-delivery pay way the courier collects, created once."""
    existing = PayWay.objects.filter(
        settlement=PaySettlement.COURIER_CASH, active=True
    ).first()
    if existing is not None:
        return existing
    return PayWayFactory.create(
        active=True,
        provider_code="cash_on_delivery",
        settlement=PaySettlement.COURIER_CASH,
    )


def courier_cash_order(**kwargs):
    """An order paid in cash on delivery, so a courier may ship it unpaid."""
    kwargs.setdefault("pay_way", courier_cash_pay_way())
    return OrderFactory(**kwargs)


def carrier_terminal_pay_way() -> PayWay:
    """BoxNow PAY ON THE GO, paid at the locker machine, created once."""
    existing = PayWay.objects.filter(
        settlement=PaySettlement.CARRIER_TERMINAL, active=True
    ).first()
    if existing is not None:
        return existing
    return PayWayFactory.create_carrier_terminal_payment()


def carrier_terminal_order(**kwargs):
    """An order paid at the BoxNow locker, so a parcel may ship it unpaid."""
    kwargs.setdefault("pay_way", carrier_terminal_pay_way())
    return OrderFactory(**kwargs)
