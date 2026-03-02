import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured

        from core.admin import override_third_party_admins
        from core.tasks import validate_task_configuration

        import core.signals.rosetta  # noqa: F401

        override_third_party_admins()

        try:
            validate_task_configuration()
        except ImproperlyConfigured as e:
            logger.error("Task configuration error: %s", e)
