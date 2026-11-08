"""Give every credit issued before allocations existed the allocation it was
issued from.

A credit document froze its VAT rows when it was issued
(``RetailCredit.vat_breakdown``), so the allocation of its source is exactly
those rows: the headroom of the receipt stays what it was, and the credit
keeps matching its allocation. Sources that have no credit yet (a refund
awaiting its receipt, an in-flight claim) are NOT backfilled: they are
allocated, oldest first, the first time anything needs the headroom
(``order.credit_allocation.ensure_allocations``), which is the rule for any
money nobody claimed.

Written against the app registry it is given, so the migration runs it on
the historical models and a test runs it on the real ones. Idempotent.
"""

from __future__ import annotations

from decimal import Decimal


def backfill_credit_allocations(apps) -> int:
    """Create the missing allocations of issued credits; returns how many
    sources were given one."""
    credit_model = apps.get_model("order", "RetailCredit")
    allocation_model = apps.get_model("order", "CreditAllocation")
    attempt_model = apps.get_model("order", "RefundAttempt")
    decision_model = apps.get_model("order", "ChargebackDecision")
    done = 0
    for credit in credit_model.objects.filter(
        mydata_status__isnull=False
    ).exclude(mydata_status="CANCELED"):
        source: dict[str, object] | None = None
        moved_at = None
        if credit.refund_id is not None:
            attempt = attempt_model.objects.filter(
                refund_id=credit.refund_id
            ).first()
            if attempt is not None:
                source = {"refund_attempt_id": attempt.pk}
            else:
                source = {"refund_id": credit.refund_id}
            moved_at = credit.refund.created_at
        elif credit.refund_attempt_id is not None:
            source = {"refund_attempt_id": credit.refund_attempt_id}
            moved_at = credit.created_at
        elif credit.chargeback_id is not None:
            decision = decision_model.objects.filter(
                chargeback_id=credit.chargeback_id
            ).first()
            if decision is not None:
                source = {"decision_id": decision.pk}
                moved_at = decision.decided_at
        if source is None or allocation_model.objects.filter(**source).exists():
            continue
        allocation_model.objects.bulk_create(
            allocation_model(
                order_id=credit.order_id,
                rate=Decimal(row["rate"]),
                amount=Decimal(row["gross"]),
                amount_currency=credit.currency,
                moved_at=moved_at,
                **source,
            )
            for row in credit.vat_breakdown
            if Decimal(row["gross"]) > 0
        )
        done += 1
    return done
