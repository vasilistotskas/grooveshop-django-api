"""What a provider reports for a payment, for tests that book one."""

from __future__ import annotations

from decimal import Decimal

from djmoney.money import Money

#: What a provider says it captured for the payment under test.
CAPTURED = Money(Decimal("50.00"), "EUR")


def captured_for(payment_id: str) -> Money:
    """What a provider collects for the order holding ``payment_id``: its
    total. ``OrderService.handle_payment_succeeded`` refuses any other
    amount, so a test about something else settles with the right one. An
    unknown intent gets zero (the handler never reads it)."""
    from order.models.order import Order

    order = Order.objects.filter(payment_id=payment_id).first()
    if order is None:
        return Money(0, "EUR")
    return order.calculate_order_total_amount()
