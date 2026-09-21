"""Settle the payment of every returned order that was never paid.

``Order.save()`` settled an unpaid ``payment_status`` only on the
CANCELED transition, so a cash-on-delivery parcel the customer refused
or never collected came back RETURNED with its payment reading "Pending"
forever — 34 rows on tenant #1 on 2026-09-20, the oldest from May. The
save now settles RETURNED the same way; this brings the rows it already
produced in line.

A queryset ``update`` on purpose: no signals, no history rows, no
emails — the parcels are long back. Irreversible by nature (the previous
value is not worth keeping), so the reverse is a no-op.
"""

from django.db import migrations

UNPAID = ("PENDING", "PROCESSING", "FAILED")


def settle(apps, schema_editor):
    Order = apps.get_model("order", "Order")
    Order.objects.filter(
        status="RETURNED", payment_status__in=UNPAID
    ).update(payment_status="CANCELED")


class Migration(migrations.Migration):
    dependencies = [("order", "0057_settle_canceled_unpaid_orders")]

    operations = [migrations.RunPython(settle, migrations.RunPython.noop)]
