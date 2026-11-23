"""``Order.confirmed_at``: when the order was confirmed and people were
told about it (``OrderLifecycleService.confirm``).

Additive: a nullable column the serving release does not know. Every order
the previous releases confirmed (an offline order at creation, an online
one when its payment went through) is stamped with its ``created_at``; an
online order never paid stays empty. Orders that release creates or settles
while this one rolls out are stamped afterwards by
``manage.py backfill_confirmed_at``, the same rule (idempotent).
"""

from django.db import migrations, models

from order.lifecycle.confirmation import backfill_confirmed_at


def stamp(apps, schema_editor):
    backfill_confirmed_at(apps)


class Migration(migrations.Migration):

    dependencies = [
        ("order", "0114_order_personal_data_erased_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="confirmed_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text=(
                    "When the order was confirmed and the customer and the "
                    "store were told about it: when it was placed, or, for "
                    "a card payment, when the payment went through. Empty "
                    "for a card order that was never paid."
                ),
                null=True,
                verbose_name="Confirmed At",
            ),
        ),
        migrations.RunPython(stamp, migrations.RunPython.noop),
    ]
