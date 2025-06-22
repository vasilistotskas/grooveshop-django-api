import logging
import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

logger = logging.getLogger(__name__)


@setup_logging.connect
def config_loggers(*args, **kwags):
    from django.conf import settings  # noqa: F401, PLC0415


def create_celery_app():
    tasker = Celery("core")
    tasker.conf.enable_utc = False
    tasker.conf.update(timezone=os.getenv("TIME_ZONE", "Europe/Athens"))
    tasker.config_from_object("django.conf:settings", namespace="CELERY")
    tasker.autodiscover_tasks()

    return tasker


celery_app = create_celery_app()
