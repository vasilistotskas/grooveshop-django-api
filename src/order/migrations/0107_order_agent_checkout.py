"""One order per agent checkout: ``Order.agent_checkout``.

The agent gateway names its checkout session on order creation, and
``OrderService`` answers a repeat with that checkout's order. The partial
unique index ``order_agent_checkout_unique`` rejects a second order that
raced the lookup, so a checkout can never place two orders.

Only adds: a nullable column (every existing order, and every order not
placed through the gateway, carries NULL) and the index over its non-NULL
values. The column is new and empty, so the index cannot meet a duplicate.

``AddUniqueConstraintAdaptively`` builds it ``CONCURRENTLY`` in the PreSync
job, where ``order_order`` is live, and plainly inside the transaction a
new store's schema is provisioned in (``docs/migrations.md``;
``0069_order_payment_id_unique`` explains why neither ``AddConstraint`` nor
``AddIndexConcurrently`` will do).
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ("order", "0106_web_url_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="agent_checkout",
            field=models.UUIDField(
                blank=True,
                editable=False,
                help_text=(
                    "The agent gateway's checkout that placed this order. A "
                    "checkout places one order: a repeat of its order "
                    "creation is answered with this order."
                ),
                null=True,
                verbose_name="Agent checkout",
            ),
        ),
        AddUniqueConstraintAdaptively(
            "order",
            models.UniqueConstraint(
                fields=["agent_checkout"],
                condition=models.Q(agent_checkout__isnull=False),
                name="order_agent_checkout_unique",
            ),
        ),
    ]
