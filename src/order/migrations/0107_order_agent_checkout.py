"""One order per agent checkout: ``Order.agent_checkout``.

The agent gateway names its checkout session on order creation, and
``OrderService`` answers a repeat with that checkout's order. The partial
unique index ``order_agent_checkout_unique`` (``0108``) rejects a second
order that raced the lookup, so a checkout can never place two orders.

Only adds a nullable column: every existing order, and every order not
placed through the gateway, carries NULL.

Atomic, and apart from the index: ``0108`` must run outside a
transaction to build ``CONCURRENTLY``, and in a non-atomic migration this
column would commit before the build. A build that failed or was
cancelled would then leave the column behind, unrecorded, and the
PreSync retry's ``ADD COLUMN`` would fail on it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
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
    ]
