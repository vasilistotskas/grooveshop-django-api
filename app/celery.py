import logging
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging
from django.conf import settings


CELERY_LOGGER_NAME = "celery"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")


@setup_logging.connect
def setup_celery_logging(loglevel=None, **kwargs):
    if loglevel:
        logging.getLogger(CELERY_LOGGER_NAME).setLevel(loglevel)


def create_celery_app():
    app = Celery("app")
    app.conf.enable_utc = False
    app.conf.update(timezone=os.environ.get("TIME_ZONE"))
    app.config_from_object(settings, namespace="CELERY")

    # Celery Beat Settings
    app.conf.beat_schedule = {
        "clear_sessions_for_none_users_task-every-week": {
            "task": "core.tasks.clear_sessions_for_none_users_task",
            "schedule": crontab(minute=0, hour=0, day_of_week="mon"),
        },
        "explorer.tasks.truncate_querylogs": {
            "task": "explorer.tasks.truncate_querylogs",
            "schedule": crontab(hour=1, minute=0),
            "kwargs": {"days": 30},
        },
    }

    app.autodiscover_tasks()

    return app


if settings.REDIS_HEALTHY:
    app = create_celery_app()

    @app.task(bind=True, name="Debug Task")
    def debug_task(self):
        debug = bool(int(os.environ.get("DEBUG", 0)))
        if debug:
            logging.debug(f"Request: {self.request!r}")

else:
    app = None
