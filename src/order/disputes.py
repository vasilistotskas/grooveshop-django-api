"""What a lost dispute leaves behind, whichever provider reported it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from djmoney.money import Money

from order.models.history import OrderHistory
from tenant.celery import dispatch_on_commit

if TYPE_CHECKING:
    from order.models.order import Order
    from order.payment_ledger import ChargebackOutcome


def announce_chargeback(
    order: Order,
    outcome: ChargebackOutcome,
    *,
    dispute_id: str,
    provider_code: str,
) -> bool:
    """Write the red history entry and email staff about a lost dispute.

    Called under no lock after ``PaymentLedger.record_chargeback``. A
    chargeback the ledger already held is announced by the delivery that
    recorded it, so nothing is done for it (``False``). One with nothing
    left to record (the payment was already refunded in full) is announced
    too: staff must know the money was taken, even though the ledger has no
    row for it. No customer email and no ``order_refunded``.
    """
    from order.tasks import send_dispute_notification_email

    if outcome.row is not None and not outcome.created:
        return False
    OrderHistory.log_chargeback(
        order,
        recorded=outcome.recorded,
        requested=outcome.requested,
        returned=Money(outcome.returned, outcome.requested.currency),
        dispute_id=dispute_id,
    )
    dispatch_on_commit(
        send_dispute_notification_email,
        [order.id, dispute_id, provider_code],
        {"lost": True},
    )
    return True
