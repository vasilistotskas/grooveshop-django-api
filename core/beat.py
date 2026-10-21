"""Make the database the beat scheduler reads agree with the settings.

``DatabaseScheduler.setup_schedule`` only inserts and updates the rows of
``CELERY_BEAT_SCHEDULE``; it never removes one whose entry is gone. A
database cloned from production (staging) therefore brings production's
rows along, and they would keep firing. ``settings.CELERY_BEAT_UNSCHEDULED_ENTRIES``
names the entries this environment must not run, and beat deletes their rows
on every start, so the clone converges whenever the scheduler restarts.
"""

import logging

from django.conf import settings
from django_celery_beat.models import PeriodicTask

logger = logging.getLogger(__name__)


def delete_unscheduled_periodic_tasks() -> int:
    """Delete the rows of every entry this environment does not schedule.

    Deleting through the ORM fires django-celery-beat's own signals, which
    bump ``PeriodicTasks.last_update`` so the running scheduler reloads.
    """
    deleted, _ = PeriodicTask.objects.filter(
        name__in=settings.CELERY_BEAT_UNSCHEDULED_ENTRIES
    ).delete()
    if deleted:
        logger.info(
            "Deleted periodic tasks this environment does not schedule",
            extra={"names": list(settings.CELERY_BEAT_UNSCHEDULED_ENTRIES)},
        )
    return deleted
