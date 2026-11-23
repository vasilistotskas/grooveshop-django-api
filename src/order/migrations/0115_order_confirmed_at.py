"""``Order.confirmed_at``: when the order was confirmed and people were
told about it (``OrderLifecycleService.confirm``).

Additive: a nullable column the serving release does not know. Every order
the previous releases confirmed (an offline order at creation, an online
one when its payment went through) is stamped with its ``created_at``; an
online order never paid stays empty. It runs before the new release
serves anything, so every order it stamps was announced by the previous
one. Those the previous release announces while the new one rolls out are
stamped afterwards by ``manage.py backfill_confirmed_at``
(``order.lifecycle.confirmation``): offline orders placed and online
payments booked before the rollout finished.
"""

from django.db import migrations, models
from django.db.models import F, Q

# The rule as it stood when this migration was written, kept here so a
# replay never depends on runtime code: an online pay way's settlement, and
# the payment statuses in which the customer's money was taken.
ONLINE_SETTLEMENT = "online"
CHARGED_PAYMENT_STATUSES = ("COMPLETED", "REFUNDED", "PARTIALLY_REFUNDED")


def stamp(apps, schema_editor):
    never_paid_online = Q(pay_way__settlement=ONLINE_SETTLEMENT) & ~Q(
        payment_status__in=CHARGED_PAYMENT_STATUSES
    )
    apps.get_model("order", "Order").objects.filter(
        confirmed_at__isnull=True
    ).exclude(never_paid_online).update(confirmed_at=F("created_at"))


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
