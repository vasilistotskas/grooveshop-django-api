from __future__ import absolute_import
from __future__ import unicode_literals

import logging
import os

from asgiref.sync import async_to_sync
from celery import Celery
from celery import shared_task
from celery.signals import setup_logging
from channels.layers import get_channel_layer

from config.logging import config_logging

CELERY_LOGGER_NAME = "celery"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
logger = logging.getLogger("celery")


@setup_logging.connect
def config_loggers(*args, **kwags):
    config_logging()


def create_celery_app():
    tasker = Celery("core")
    tasker.conf.enable_utc = False
    tasker.conf.update(timezone=os.getenv("TIME_ZONE", "Europe/Athens"))
    tasker.config_from_object("django.conf:settings", namespace="CELERY")
    tasker.autodiscover_tasks()

    return tasker


celery_app = create_celery_app()


@shared_task(bind=True, name="Debug Task")
def debug_task(self):
    debug = os.getenv("DEBUG", "True") == "True"
    if debug:
        print(f"Request: {self.request!r}")
        logger.debug(f"Request: {self.request!r}")


@shared_task(bind=True, name="Debug Task Notification")
def debug_task_notification(self):
    debug = os.getenv("DEBUG", "True") == "True"
    if debug:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "notifications",
            {
                "type": "send_notification",
                "user": 1,
                "seen": False,
                "link": "https://www.google.com",
                "kind": "info",
                "translations": [
                    {
                        "en": {
                            "message": "This is a test notification",
                        },
                        "el": {
                            "message": "Αυτή είναι μια δοκιμαστική ειδοποίηση",
                        },
                    },
                ],
            },
        )
        print(f"Request: {self.request!r}, Notification sent.")
        logger.debug(f"Logger Request: {self.request!r}, Notification sent.")
