"""Code owns the beat schedule; the database only follows it.

``CELERY_BEAT_SCHEDULE`` is the whole schedule. ``DatabaseScheduler`` writes
each of its entries to a row when it starts, but never removes a row whose
entry is gone, so a removed entry keeps firing at a task that may no longer
exist, and a database cloned from production brings production's entries
along. Beat therefore deletes every row the settings do not declare on each
start. The row stays what the admin's Enabled switch, ``ScheduleGate`` and
"did it run" (``last_run_at``) read.
"""

import logging

from django.conf import settings
from django_celery_beat.models import PeriodicTask

logger = logging.getLogger(__name__)

#: ``DatabaseScheduler`` installs this one itself, because
#: ``CELERY_RESULT_EXPIRES`` is set; it is never in the settings' dict.
BACKEND_CLEANUP_ENTRY = "celery.backend_cleanup"


def delete_undeclared_periodic_tasks() -> int:
    """Delete every row this environment's schedule does not declare.

    Deleting through the ORM fires django-celery-beat's own signals, which
    bump ``PeriodicTasks.last_update`` so a running scheduler reloads.
    """
    undeclared = PeriodicTask.objects.exclude(
        name__in=[*settings.CELERY_BEAT_SCHEDULE, BACKEND_CLEANUP_ENTRY]
    )
    names = list(undeclared.values_list("name", flat=True))
    deleted, _ = undeclared.delete()
    if names:
        logger.info(
            "Deleted periodic tasks the schedule does not declare",
            extra={"names": names},
        )
    return deleted
