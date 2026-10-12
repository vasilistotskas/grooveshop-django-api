"""Rename ``Order.paid_amount`` to ``Order.amount_due`` — step one of two.

``paid_amount`` was never what was paid. Checkout writes it at creation
from ``calculate_order_total_amount()`` (lines and extras, less every
deduction), before any money moves, so it is what the customer OWES.
Readers took it for money collected: ``pricing_breakdown.remaining_amount``
was 0 on every order and the ``is_paid`` filter matched every unpaid
online order. The field now carries its real name; what is still to be
paid is ``Order.remaining_amount``, read from ``payment_status``.

A deploy runs its migrations in the PreSync hook BEFORE the new pods
exist, and the previous release keeps selecting and inserting
``paid_amount`` until its last pod exits, so a rename in one step would
break it for the length of the rollout. This is the expand step:

* ``amount_due`` is added NOT NULL with a ``db_default`` (the previous
  release's INSERTs do not name it) and copied from ``paid_amount``.
  django-money builds the companion currency column itself and has no
  ``db_default`` for it, so that column is added with the SQL Django would
  emit minus the ``DROP DEFAULT`` that follows (the same trap
  ``0049_invoice_total_components`` patched after the fact) and the state
  is told separately.
* ``paid_amount`` and its currency column leave Django's STATE only. Both
  are nullable, so the new code's INSERTs, which no longer name them, still
  work. The columns stay in the database.

The API expands the same way. This release still emits ``paidAmount``
(top level, and in ``pricingBreakdown``) beside ``amountDue``, both from
``amount_due`` and both marked ``deprecated`` in the schema: the
storefront's and the agent gateway's generated schemas require
``paidAmount``, so removing it in the release that adds ``amountDue``
would fail every order response they parse between the two rollouts. The
``paidAmount*`` filters and ordering are gone outright (nothing outside
Django uses them).

Until the re-copy below runs, an order the previous release created has
``amount_due`` 0 (its INSERTs do not name the column): ``mark_as_paid`` and
the Viva webhook set it from the order total when the payment lands, as
they always did for a zero. Run the re-copy statement on every schema right
after the rollout if you do not want to wait for release N+1.

Release N+1 (contract) -- the exact steps
=========================================

1. **Ship it only after the storefront and the agent gateway read
   ``amountDue``** (regenerated clients, every storefront variant, the
   gateway's ``PricingBreakdown`` DTO) and those releases are what
   production runs. It removes what they still require.
2. **Drop the two deprecated API fields**: ``OrderSerializer.paid_amount``
   (the field, its entry in ``Meta.fields``, the ``deprecate_fields``
   argument of ``extend_schema_serializer``) and the ``"paid_amount"`` key
   of ``OrderDetailSerializer.get_pricing_breakdown`` (and of its
   ``extend_schema_field`` properties). Regenerate ``schema.yml``.
3. **A migration with ``contract_of``**, the only way ``migration_preflight``
   accepts a ``DROP`` (``RemoveField`` cannot: the fields left the state
   here). It re-copies what the previous release wrote while it still
   served, then drops both columns::

       class Migration(migrations.Migration):
           dependencies = [("order", "<latest>")]
           contract_of = [("order", "0070_order_amount_due")]
           operations = [
               migrations.RunSQL(
                   "UPDATE order_order "
                   "SET amount_due = paid_amount, "
                   "amount_due_currency = COALESCE(paid_amount_currency, 'EUR') "
                   "WHERE amount_due = 0 AND paid_amount > 0; "
                   "ALTER TABLE order_order "
                   "DROP COLUMN IF EXISTS paid_amount, "
                   "DROP COLUMN IF EXISTS paid_amount_currency",
                   reverse_sql=(
                       "ALTER TABLE order_order "
                       "ADD COLUMN IF NOT EXISTS paid_amount numeric(11, 2) NULL, "
                       "ADD COLUMN IF NOT EXISTS paid_amount_currency varchar(3) NULL"
                   ),
               ),
           ]

   A deploy that skips this release (0070 still pending next to its
   contract) is refused by the preflight.
4. Update ``docs/order-system.md`` (§7.3 and the invariants row) to drop
   the "deprecated alias" wording.
"""

from decimal import Decimal

import djmoney.models.fields
import djmoney.money
from django.db import migrations

_ADD_CURRENCY = """
ALTER TABLE order_order
    ADD COLUMN amount_due_currency varchar(3) NOT NULL DEFAULT 'EUR';
"""

_DROP_CURRENCY = """
ALTER TABLE order_order DROP COLUMN amount_due_currency;
"""

# Rows with a NULL paid_amount keep the column defaults (0 and EUR).
_BACKFILL = """
UPDATE order_order
SET amount_due = paid_amount,
    amount_due_currency = COALESCE(paid_amount_currency, 'EUR')
WHERE paid_amount IS NOT NULL;
"""

_BACKFILL_REVERSE = """
UPDATE order_order
SET paid_amount = amount_due,
    paid_amount_currency = amount_due_currency;
"""


class Migration(migrations.Migration):
    dependencies = [("order", "0069_order_payment_id_unique")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="amount_due",
            field=djmoney.models.fields.MoneyField(
                db_default=Decimal("0"),
                decimal_places=2,
                default=djmoney.money.Money(0, "EUR"),
                help_text=(
                    "What the customer owes for the order, fixed at "
                    "creation: lines and extras, less every deduction. "
                    "Not what was collected; that is on the payment "
                    "ledger."
                ),
                max_digits=11,
                verbose_name="Amount Due",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="order",
                    name="amount_due_currency",
                    field=djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(_ADD_CURRENCY, _DROP_CURRENCY),
            ],
        ),
        migrations.RunSQL(_BACKFILL, _BACKFILL_REVERSE),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="order", name="paid_amount_currency"
                ),
                migrations.RemoveField(model_name="order", name="paid_amount"),
            ],
            database_operations=[],
        ),
    ]
