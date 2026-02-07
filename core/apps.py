import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured

        from core.tasks import validate_task_configuration

        try:
            validate_task_configuration()
        except ImproperlyConfigured as e:
            logger.error("Task configuration error: %s", e)
