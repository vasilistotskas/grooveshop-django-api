"""Delete the beat row of the retired ``warn-unprinted-acs-vouchers`` task.

django-celery-beat's ``DatabaseScheduler`` only inserts and updates the
``CELERY_BEAT_SCHEDULE`` entries (``setup_schedule`` →
``update_from_dict``); it never removes a row whose entry is gone. Left
alone, the row keeps firing ``tenant.tasks.fanout_warn_unprinted_acs_vouchers``
every weekday, a task that no longer exists.

``tenant`` is a SHARED_APPS-only app, so this runs in the public schema,
where ``django_celery_beat`` keeps its tables. Historical models fire no
signals, so ``PeriodicTasks.last_update`` is bumped by hand: a beat
process still running the previous release then reloads its schedule
instead of holding the deleted entry in memory.
"""

from __future__ import annotations

from django.db import migrations
from django.utils import timezone

TASK_NAME = "warn-unprinted-acs-vouchers"


def delete_beat_row(apps, schema_editor):
    periodic_task = apps.get_model("django_celery_beat", "PeriodicTask")
    periodic_tasks = apps.get_model("django_celery_beat", "PeriodicTasks")

    deleted, _ = periodic_task.objects.filter(name=TASK_NAME).delete()
    if deleted:
        periodic_tasks.objects.update_or_create(
            ident=1, defaults={"last_update": timezone.now()}
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0048_store_description_i18n"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(delete_beat_row, migrations.RunPython.noop),
    ]
