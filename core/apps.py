import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured

        import core.signals.rosetta  # noqa: F401

        from core.tasks import validate_task_configuration

        try:
            validate_task_configuration()
        except ImproperlyConfigured as e:
            logger.error("Task configuration error: %s", e)

        # Prime the in-memory gettext catalogs with DB-backed msgstrs
        # before the first request. Safe during `migrate`: if the
        # Translation table doesn't exist yet, the overlay returns
        # without doing anything (see apply_db_overlay).
        try:
            from core.rosetta_storage import apply_db_overlay

            apply_db_overlay()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Initial translation overlay skipped: %s", exc)
