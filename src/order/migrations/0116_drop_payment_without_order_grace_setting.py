"""Delete the retired ``PAYMENT_WITHOUT_ORDER_GRACE_MINUTES`` setting rows.

The wait was for a payment-first intent, whose order could still be being
created when its payment succeeded. That path is gone (#818): every intent
is opened for an order that already exists, so a succeeded one no order
carries is alerted at once and nothing reads the setting.

The previous release reads it through ``store_setting``, which answers a
missing row with its declared default, so it keeps working while it still
serves traffic.

Reverse is a no-op: the code that read the setting is gone, so restoring
the row would be a lie.
"""

from django.db import migrations


def drop_setting(apps, _schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.filter(name="PAYMENT_WITHOUT_ORDER_GRACE_MINUTES").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0115_order_confirmed_at"),
        # The Setting model must exist before its rows are deleted.
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_setting, migrations.RunPython.noop),
    ]
