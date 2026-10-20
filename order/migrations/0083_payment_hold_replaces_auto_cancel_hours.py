"""Carry the old 24 hour rule over to the orders already waiting.

``ORDER_AUTO_CANCEL_PENDING_HOURS`` (default 24) is replaced by the hold
the payment link itself carries (``ORDER_ONLINE_PAYMENT_HOLD_MINUTES``,
``order/payment_hold.py``). An unpaid online order created before this
release has no ``payment_hold_expires_at``, yet a payment session opened
for it may still be payable for up to a day (Stripe's default session
lifetime). Releasing its stock on the new, shorter hold could cancel an
order a customer is still entitled to pay, so each such order keeps the
24 hours it was promised: it is stamped ``created_at + 24h``.

The retired setting's rows are deleted. The previous release reads it with
a default, so it keeps working while it still serves traffic.

Reverse is a no-op: the code that read the setting is gone, so restoring
the row would be a lie.
"""

from datetime import timedelta

from django.db import migrations
from django.db.models import F

LEGACY_HOLD = timedelta(hours=24)


def carry_over(apps, _schema_editor):
    Order = apps.get_model("order", "Order")
    Order.objects.filter(
        status="PENDING",
        payment_status="PENDING",
        pay_way__settlement="ONLINE",
        payment_hold_expires_at__isnull=True,
    ).update(payment_hold_expires_at=F("created_at") + LEGACY_HOLD)

    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.filter(name="ORDER_AUTO_CANCEL_PENDING_HOURS").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0082_order_payment_hold_expires_at"),
        ("pay_way", "0001_initial"),
        # The Setting model must exist before its rows are deleted.
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(carry_over, migrations.RunPython.noop),
    ]
