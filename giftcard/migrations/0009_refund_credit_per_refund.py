"""A card is credited back for an order in parts, each naming its refund.

``unique_giftcard_refund_per_order`` allowed one REFUND_CREDIT per card per
order, which is what a refund of the whole redemption needed and what a
refund in parts cannot live with. It is replaced, not just dropped:

* ``refund_attempt`` (nullable, no index: nothing looks a credit up by it)
  names the refund that made a credit.
* ``unique_giftcard_whole_refund_per_order`` is the same guard restricted to
  credits that name no refund attempt. The release still serving while this
  migration is applied writes exactly those (it knows no ``refund_attempt``
  column, so every row it inserts has NULL there) and keeps its protection
  against two racing credit tasks, from the moment the old constraint goes:
  the new one is built first, concurrently. A credit that names its refund
  is a part of a refund and is not limited.

Existing duplicates cannot abort the build: every row it covers is already
covered by the constraint it replaces.
"""

import django.db.models.deletion
from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ("giftcard", "0008_purchase_provenance"),
        ("order", "0079_refund_attempt_stored_value"),
    ]

    operations = [
        migrations.AddField(
            model_name="giftcardtransaction",
            name="refund_attempt",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                help_text="The refund that returned this credit, when it was one.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="gift_card_transactions",
                to="order.refundattempt",
                verbose_name="Refund attempt",
            ),
        ),
        AddUniqueConstraintAdaptively(
            "giftcardtransaction",
            models.UniqueConstraint(
                condition=models.Q(
                    ("kind", "REFUND_CREDIT"), ("refund_attempt__isnull", True)
                ),
                fields=("gift_card", "order"),
                name="unique_giftcard_whole_refund_per_order",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="giftcardtransaction",
            name="unique_giftcard_refund_per_order",
        ),
    ]
