"""Delete the beat row of the retired ``check-pending-orders`` task.

django-celery-beat's ``DatabaseScheduler`` never removes a row whose
``CELERY_BEAT_SCHEDULE`` entry is gone (see ``0049``), so the row would
keep firing ``tenant.tasks.fanout_check_pending_orders`` every morning, a
task that no longer exists.

``tenant`` is a SHARED_APPS-only app, so this runs in the public schema,
where ``django_celery_beat`` keeps its tables. ``PeriodicTasks.last_update``
is bumped by hand so a beat process still running the previous release
reloads its schedule.
"""

from __future__ import annotations

from django.db import migrations
from django.utils import timezone

TASK_NAME = "check-pending-orders"


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
        ("tenant", "0055_acs_and_boxnow_help_texts"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(delete_beat_row, migrations.RunPython.noop),
    ]
