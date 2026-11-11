"""Give every order that was already paid its payment ledger rows.

``PaymentTransaction`` (0066) is where an order's captured and refunded
money is read from from now on. Orders paid before it existed have none,
so this writes what the order rows and ``metadata["refunds"]`` prove:

* **CHARGE** for an order paid through Stripe or Viva: provider and
  reference are the order's ``payment_id``, the amount is
  ``Order.paid_amount``.
* **COD_COLLECTION** for an order a courier collected cash for
  (``payment_method`` ``acs_cod`` / ``boxnow_cod``).
* **REFUND**, attached to that charge, for each ``metadata["refunds"]``
  entry that names a provider refund id and an amount, and one more row
  for any Stripe ``charge.refunded`` total the named refunds do not
  already cover.

What it CANNOT reconstruct, and deliberately does not guess:

* The amount the provider actually captured. ``paid_amount`` is what the
  order owed when it was created, which checkout verified against the
  provider (to the cent for Stripe, within one cent for Viva), so it is
  the best figure on file.
* Any payment but the last. An order paid twice under two Viva checkout
  codes keeps only the latest transaction id in ``payment_id``.
* A COD collection's courier reference. The row is keyed
  ``order-<id>`` rather than the voucher number, so no payout is matched
  to it.
* A refund no provider id was kept for. ``metadata["refunds"]`` entries
  with a null ``refund_id`` are the four production Viva orders whose
  refund was never executed (#134): their money is still with the
  merchant, so the ledger shows the payment as refundable, which is true.
  A Stripe webhook entry carries the charge's cumulative refunded total
  and no refund id, so it becomes one row keyed by its event id.
* Anything written while this migration ran by pods of the previous
  release. A payment recorded in that window is read from the provider
  the first time a refund needs it.

``order.payment_status`` is left alone: it is derived from the ledger by
the code that writes the next refund.

Idempotent: rows are keyed on the ledger's own unique constraint, so a
replay adds nothing. Not reversible by design (the rows are the record).
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

import moneyed
from django.db import migrations
from djmoney.money import Money

CHARGE = "CHARGE"
REFUND = "REFUND"
COD_COLLECTION = "COD_COLLECTION"

PAID_STATUSES = ("COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED")
STRIPE = "stripe"
VIVA = "viva_wallet"
STRIPE_PAYMENT_METHODS = ("stripe", "stripe_agent_spt")
COD_PAYMENT_METHODS = ("acs_cod", "boxnow_cod")
STRIPE_PAYMENT_INTENT_PREFIX = "pi_"
#: What the old code wrote as the amount of a refund of everything.
FULL_REFUND_MARKER = "full"


def _decimal(value) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _online_provider(row) -> str | None:
    """The provider that captured an online order, else ``None``."""
    code = row["pay_way__provider_code"]
    if code in (STRIPE, VIVA):
        return code
    method = row["payment_method"]
    payment_id = row["payment_id"] or ""
    if method in STRIPE_PAYMENT_METHODS or payment_id.startswith(
        STRIPE_PAYMENT_INTENT_PREFIX
    ):
        return STRIPE
    if method == VIVA:
        return VIVA
    return None


def _planned_refunds(entries, charge_amount: Decimal):
    """``(reference, amount, entry)`` for what ``metadata["refunds"]`` proves.

    Entries with a provider refund id count for what they say. Stripe's
    ``charge.refunded`` entries carry the charge's CUMULATIVE refunded
    total (minor units) and no refund id, and include what the in-app
    refunds already paid back, so only the part the named refunds do not
    cover is a row of its own.
    """
    planned = []
    named = Decimal(0)
    for entry in entries:
        reference = entry.get("refund_id")
        if not reference:
            continue
        raw = entry.get("amount")
        amount = (
            charge_amount - named
            if raw == FULL_REFUND_MARKER
            else _decimal(raw)
        )
        if amount is None or amount <= 0:
            continue
        planned.append((str(reference), amount, entry))
        named += amount

    cumulative = Decimal(0)
    reported_by = None
    for entry in entries:
        minor = entry.get("amount_refunded")
        event_id = entry.get("stripe_event_id")
        currency = moneyed.CURRENCIES.get((entry.get("currency") or "").upper())
        if not (event_id and currency and isinstance(minor, int)):
            continue
        total = Decimal(minor) / currency.sub_unit
        if total >= cumulative:
            cumulative, reported_by = total, entry
    if reported_by is not None and cumulative > named:
        planned.append(
            (reported_by["stripe_event_id"], cumulative - named, reported_by)
        )
    return planned


def backfill_payment_transactions(apps, schema_editor):
    Order = apps.get_model("order", "Order")
    PaymentTransaction = apps.get_model("order", "PaymentTransaction")

    rows = (
        Order.objects.filter(payment_status__in=PAID_STATUSES)
        .values(
            "pk",
            "payment_id",
            "payment_method",
            "paid_amount",
            "paid_amount_currency",
            "metadata",
            "pay_way__provider_code",
        )
        .order_by("pk")
    )
    for row in rows.iterator(chunk_size=500):
        paid = _decimal(row["paid_amount"])
        if paid is None or paid <= 0:
            # Settled by a gift card or discount: no provider moved money.
            continue
        money = Money(paid, row["paid_amount_currency"])

        if row["payment_method"] in COD_PAYMENT_METHODS:
            PaymentTransaction.objects.get_or_create(
                provider=row["payment_method"],
                provider_reference=f"order-{row['pk']}",
                kind=COD_COLLECTION,
                defaults={"order_id": row["pk"], "amount": money},
            )
            continue

        provider = _online_provider(row)
        payment_id = row["payment_id"]
        if provider is None or not payment_id:
            continue

        charge = PaymentTransaction.objects.filter(
            provider=provider, provider_reference=payment_id, kind=CHARGE
        ).first()
        if charge is None:
            charge = PaymentTransaction.objects.create(
                order_id=row["pk"],
                kind=CHARGE,
                provider=provider,
                provider_reference=payment_id,
                amount=money,
            )
        elif charge.order_id != row["pk"]:
            # Two orders naming one payment id: not this migration's call.
            continue

        entries = (row["metadata"] or {}).get("refunds") or []
        for reference, amount, entry in _planned_refunds(entries, paid):
            refund, created = PaymentTransaction.objects.get_or_create(
                provider=provider,
                provider_reference=reference,
                kind=REFUND,
                defaults={
                    "order_id": row["pk"],
                    "charge": charge,
                    "amount": Money(amount, row["paid_amount_currency"]),
                    "description": (entry.get("reason") or "")[:255],
                },
            )
            refunded_at = entry.get("refunded_at")
            if created and refunded_at:
                try:
                    stamp = datetime.fromisoformat(refunded_at)
                except ValueError:
                    continue
                PaymentTransaction.objects.filter(pk=refund.pk).update(
                    created_at=stamp
                )


class Migration(migrations.Migration):
    dependencies = [("order", "0066_payment_transaction")]

    operations = [
        migrations.RunPython(
            backfill_payment_transactions, migrations.RunPython.noop
        )
    ]
