"""The staff decision on a lost dispute: did the goods come back?

A chargeback takes money back and says nothing about the goods. Whether the
sale was reversed (the goods came back or never arrived: a retail credit
document, ν.4308/2014 art. 12(4)) or stands (the customer kept them) is a
fact only staff know, so they record it once per order here.
:func:`decide_chargebacks` is the one writer; the credit itself is created by
``order.credit_documents.create_pending_credits``, as for any refund.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext

from order.credit_documents import request_credit_documents
from order.enum.payment_transaction import PaymentTransactionKind
from order.exceptions import OrderServiceError
from order.models import (
    ChargebackDecision,
    Invoice,
    Order,
    OrderHistory,
    PaymentTransaction,
)


class ChargebackDecisionError(OrderServiceError):
    """The decision cannot be recorded, or cannot be changed."""


def undecided_chargebacks(order: Order):
    """The order's CHARGEBACK rows nobody has decided yet."""
    return PaymentTransaction.objects.filter(
        order=order,
        kind=PaymentTransactionKind.CHARGEBACK,
        decision__isnull=True,
    )


def decide_chargebacks(
    order_id: int, *, goods_returned: bool, user
) -> list[ChargebackDecision]:
    """Record that the goods of the order's lost disputes came back
    (*goods_returned*) or that the customer kept them, for every CHARGEBACK
    row of the order; returns the decisions written or changed.

    Idempotent: a row already decided the same way is left alone, so a
    second click or a retried request changes nothing and credits nothing
    twice. The decision can be changed until the order has a receipt row:
    from then on a "returned" decision has either netted that receipt or
    owes it a credit document, and taking it back would leave the receipt
    and the credit disagreeing with the books. A credit document is
    cancelled through its own flow, never by flipping the decision.

    Raises:
        ChargebackDecisionError: the order has no chargeback, or a row was
            decided the other way and its receipt exists.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        rows = list(
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGEBACK
            )
            .select_related("decision")
            .order_by("created_at", "id")
        )
        if not rows:
            raise ChargebackDecisionError(
                gettext("Order #%(order_id)s has no chargeback to decide.")
                % {"order_id": order.id}
            )

        flips_a_returned_decision = goods_returned is False and any(
            hasattr(row, "decision") and row.decision.goods_returned
            for row in rows
        )
        if (
            flips_a_returned_decision
            and Invoice.objects.filter(order=order).exists()
        ):
            raise ChargebackDecisionError(
                gettext(
                    "Order #%(order_id)s already has a receipt, so the "
                    "decision that the goods came back can no longer be "
                    "changed."
                )
                % {"order_id": order.id}
            )

        changed: list[ChargebackDecision] = []
        now = timezone.now()
        for row in rows:
            decision = getattr(row, "decision", None)
            if decision is None:
                decision = ChargebackDecision.objects.create(
                    chargeback=row,
                    goods_returned=goods_returned,
                    decided_by=user,
                    decided_at=now,
                )
            elif decision.goods_returned != goods_returned:
                decision.goods_returned = goods_returned
                decision.decided_by = user
                decision.decided_at = now
                decision.save(
                    update_fields=["goods_returned", "decided_by", "decided_at"]
                )
            else:
                continue
            changed.append(decision)

        for decision in changed:
            template = (
                gettext(
                    "Chargeback %(amount)s: goods returned, the sale is "
                    "reversed (decided by %(who)s)."
                )
                if goods_returned
                else gettext(
                    "Chargeback %(amount)s: the customer kept the goods, "
                    "the sale stands (decided by %(who)s)."
                )
            )
            # No ``user=``: the history's actor is a same-schema foreign
            # key, and a store's staff are platform identities.
            OrderHistory.log_note(
                order=order,
                note=template
                % {"amount": decision.chargeback.amount, "who": user},
            )
        if goods_returned and changed:
            request_credit_documents(order.id)
    return changed
