"""One order per agent checkout: ``order_agent_checkout_unique``.

The partial unique index over the non-NULL ``Order.agent_checkout``
values (``0107``). The column is new and empty, so the index cannot meet
a duplicate.

``AddUniqueConstraintAdaptively`` builds it ``CONCURRENTLY`` in the PreSync
job, where ``order_order`` is live, and plainly inside the transaction a
new store's schema is provisioned in (``docs/migrations.md``;
``0069_order_payment_id_unique`` explains why neither ``AddConstraint`` nor
``AddIndexConcurrently`` will do). A re-run after a failed build is safe:
the operation drops an INVALID leftover first and uses ``IF NOT EXISTS``.
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ("order", "0107_order_agent_checkout"),
    ]

    operations = [
        AddUniqueConstraintAdaptively(
            "order",
            models.UniqueConstraint(
                fields=["agent_checkout"],
                condition=models.Q(agent_checkout__isnull=False),
                name="order_agent_checkout_unique",
            ),
        ),
    ]
