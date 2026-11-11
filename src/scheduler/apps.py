from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SchedulerConfig(AppConfig):
    """The state of the periodic schedule; the schedule itself is
    ``CELERY_BEAT_SCHEDULE``.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/scheduler.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "scheduler"
    verbose_name = _("Background Jobs")
