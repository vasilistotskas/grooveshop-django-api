"""A cancelled order only promises a refund if money was actually taken.

``order.paid_amount`` reads like "what the customer paid". It is not:
``OrderService`` sets it at creation from
``calculate_order_total_amount()``, before anything is charged — it is
the amount PAYABLE. The cancellation email gated its refund paragraph
on that field alone, so every cancelled order was told its money was on
the way back, including cash-on-delivery and BOX NOW Αντικαταβολή ones
where the shopper had never paid a cent (order #274, 2026-09-10).

``is_paid`` is the settlement authority — only ``mark_as_paid`` and the
provider webhooks set ``payment_status`` COMPLETED.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db

TEMPLATES = (
    "emails/order/order_canceled.html",
    "emails/order/order_canceled.txt",
)


def _order(payment_status, paid_amount) -> object:
    """Build the exact state under test.

    ``OrderFactory`` randomises ``paid_amount`` and zeroes it for a
    COMPLETED order, so leaving either to the fixture makes the
    assertion depend on the draw rather than the template.
    """
    order = OrderFactory(payment_status=payment_status)
    order.paid_amount = paid_amount
    order.save(update_fields=["paid_amount"])
    order.refresh_from_db()
    return order


def _render(order) -> dict[str, str]:
    return {
        name: render_to_string(name, {"order": order}) for name in TEMPLATES
    }


def _mentions_refund(body: str) -> bool:
    lowered = body.lower()
    return "refund" in lowered or "επιστροφή" in lowered


class TestUnpaidOrders:
    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.PENDING,
            PaymentStatus.PROCESSING,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_no_refund_is_promised(self, payment_status):
        """The COD / PAY ON THE GO case: an amount is on the order from
        the moment it is created, but nothing has been collected."""
        order = _order(payment_status, 25)
        assert order.paid_amount.amount > 0, "the amount must be non-zero"

        for name, body in _render(order).items():
            assert not _mentions_refund(body), name


class TestPaidOrders:
    def test_a_settled_order_still_gets_its_refund_line(self):
        """The fix must not silence the case the paragraph exists for."""
        order = _order(PaymentStatus.COMPLETED, 25)

        for name, body in _render(order).items():
            assert _mentions_refund(body), name

    def test_a_fully_discounted_order_promises_nothing(self):
        """A gift card or 100% promotion can settle an order for zero.
        ``is_paid`` is true there by design, but there is no money to
        send back — see the ``is_paid`` docstring."""
        order = _order(PaymentStatus.COMPLETED, 0)

        for name, body in _render(order).items():
            assert not _mentions_refund(body), name
