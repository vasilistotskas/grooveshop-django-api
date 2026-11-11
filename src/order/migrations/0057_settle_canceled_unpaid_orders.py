"""Settle the payment of every canceled order that was never paid.

``OrderService.cancel_order`` flipped ``status`` and left
``payment_status`` alone unless it refunded, so a canceled COD or
unpaid Viva order read "Pending" forever — 78 rows on tenant #1 alone
on 2026-09-18. The service now settles them to CANCELED; this brings the
rows it already produced in line.

A queryset ``update`` on purpose: no signals, no history rows, no
emails — the orders are long closed. Irreversible by nature (the
previous value is not worth keeping), so the reverse is a no-op.
"""

from django.db import migrations

UNPAID = ("PENDING", "PROCESSING", "FAILED")


def settle(apps, schema_editor):
    Order = apps.get_model("order", "Order")
    Order.objects.filter(
        status="CANCELED", payment_status__in=UNPAID
    ).update(payment_status="CANCELED")


class Migration(migrations.Migration):
    dependencies = [("order", "0056_orderitem_recommendation_impression")]

    operations = [migrations.RunPython(settle, migrations.RunPython.noop)]
