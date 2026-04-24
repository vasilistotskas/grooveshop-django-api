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

        # Intentionally no apply_db_overlay() here: ready() runs during
        # Django setup, which can be triggered at import time by
        # wsgi/__init__.py's application(...) warmup call. In pytest
        # collection the DB is blocked for tests without the django_db
        # mark (RuntimeError), and even with try/except guards the
        # error was surfacing through the import chain. The overlay now
        # fires on the first request via TranslationReloadMiddleware —
        # see core/middleware/translation_reload.py.
