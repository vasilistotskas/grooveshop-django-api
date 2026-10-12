"""Give every existing discounted order its discount allocation.

``OrderItem.discount_allocation`` (0072) is written at checkout from now
on. Orders that predate it hold 0 on every line, which the tax documents
refuse, so each gets the proportional allocation its documents were issued
under (``order.discount_snapshot``: the redemption rows record no lines, so
nothing better exists). ``Invoice`` rows are not touched.

Split from the ADD COLUMN migration on purpose, like ``0065``. Orders the
previous release creates while it still serves the rollout are not covered
by this migration (it has already run); ``manage.py
heal_discount_allocation --all-tenants`` after the rollout covers them, and
is the same idempotent function.
"""

from django.db import migrations

from order.discount_snapshot import heal_discount_allocation


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0072_orderitem_discount_allocation"),
    ]

    operations = [
        migrations.RunPython(
            lambda apps, schema_editor: heal_discount_allocation(apps),
            migrations.RunPython.noop,
        ),
    ]
