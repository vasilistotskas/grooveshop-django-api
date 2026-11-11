"""Retire the pending-order reminder emails (expand step).

The reminder asked an unpaid online order's customer to come back after
24 hours, but the 15-minute ``auto-cancel-stuck-pending-orders`` sweep
cancels that same order at 24 hours, so the daily 07:00 reminder only
ever met an order inside the 15 minutes before its cancellation (about
1% of them), and never a second time. Once an unpaid online order holds
its stock for the payment link's lifetime, not 24 hours, it never gets
that far. The task, its template and its four settings are gone.

* ``reminder_count`` is NOT NULL, and the release still serving inserts
  orders without a value for it once this release has dropped the field
  from its model, so it gets a ``db_default`` first.
* Both columns leave Django's state only; the next release drops them
  (docs/migrations.md, "The release still serving reads the new schema").
* The four ``PENDING_ORDER_REMINDER_*`` setting rows are deleted from
  every store, so the admin Settings list stops offering switches that
  do nothing. The previous release reads them with a default.

Reverse is a no-op for the rows: restoring settings that do nothing would
only mislead.
"""

from django.db import migrations, models

REMINDER_SETTINGS = (
    "PENDING_ORDER_REMINDER_MAX_COUNT",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_1",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_2",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_3",
)


def drop_reminder_settings(apps, schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.using(schema_editor.connection.alias).filter(
        name__in=REMINDER_SETTINGS
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0083_payment_hold_replaces_auto_cancel_hours"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="reminder_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                db_default=0,
                verbose_name="Reminder Count",
                help_text="Number of pending-order reminder emails sent.",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="order", name="reminder_count"
                ),
                migrations.RemoveField(
                    model_name="order", name="last_reminder_sent_at"
                ),
            ],
        ),
        migrations.RunPython(drop_reminder_settings, migrations.RunPython.noop),
    ]
