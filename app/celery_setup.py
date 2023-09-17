from __future__ import absolute_import
from __future__ import unicode_literals

import logging
import os

from asgiref.sync import async_to_sync
from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging
from channels.layers import get_channel_layer
from django.conf import settings


CELERY_LOGGER_NAME = "celery"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")


@setup_logging.connect
def setup_celery_logging(loglevel=None, **kwargs):
    if loglevel:
        logging.getLogger(CELERY_LOGGER_NAME).setLevel(loglevel)


def create_celery_app():
    tasker = Celery("app")
    tasker.conf.enable_utc = False
    tasker.conf.update(timezone=os.environ.get("TIME_ZONE"))
    tasker.config_from_object("django.conf:settings", namespace="CELERY")

    # Celery Beat Settings
    tasker.conf.beat_schedule = {
        "clear_sessions_for_none_users_task-every-week": {
            "task": "core.tasks.clear_sessions_for_none_users_task",
            "schedule": crontab(minute=0, hour=0, day_of_week="mon"),
        }
    }

    tasker.autodiscover_tasks()

    return tasker


if settings.REDIS_HEALTHY:
    app = create_celery_app()

    @app.task(bind=True, name="Debug Task")
    def debug_task(self):
        debug = bool(int(os.environ.get("DEBUG", 0)))
        if debug:
            logging.debug(f"Request: {self.request!r}")

    @app.task(bind=True, name="Debug Task Notification")
    def debug_task_notification(self):
        debug = bool(int(os.environ.get("DEBUG", 0)))
        if debug:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "notifications",
                {
                    "type": "send_notification",
                    "message": "Debug Task Notification Message",
                },
            )
            logging.debug(f"Request: {self.request!r}, Notification sent.")

else:
    app = None
