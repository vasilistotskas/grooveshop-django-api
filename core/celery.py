import logging
import os

from celery import Celery
from celery.signals import setup_logging, task_prerun, worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

logger = logging.getLogger(__name__)

# Mirrors the version tick tracked by core.middleware.translation_reload
# on web pods. Workers don't serve HTTP, so they need their own check
# to pick up Rosetta edits without a pod restart.
_worker_translation_version: float | None = None


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from django.conf import settings  # noqa: F401, PLC0415


def create_celery_app():
    tasker = Celery("core")

    # Load Django settings first (CELERY_* namespace)
    tasker.config_from_object("django.conf:settings", namespace="CELERY")

    # Override with explicit values AFTER config_from_object
    # so these take precedence over settings.py
    tasker.conf.update(
        enable_utc=True,
        timezone=os.getenv("TIME_ZONE", "Europe/Athens"),
        # Close database connections after each task
        worker_pool_restarts=True,
        # Don't store task results in database to avoid connection issues
        task_ignore_result=False,
        # Acknowledge tasks after execution to prevent re-execution on connection errors
        task_acks_late=True,
        # Prefetch only 1 task at a time to reduce connection pool pressure
        worker_prefetch_multiplier=1,
        # Set reasonable time limits
        task_soft_time_limit=300,  # 5 minutes
        task_time_limit=600,  # 10 minutes
    )

    tasker.autodiscover_tasks()

    # Minimal connection management - let Django handle most of it
    from celery.signals import worker_process_shutdown

    @worker_process_init.connect
    def init_worker_process(**kwargs):
        """Initialize worker process with clean connections + translations."""
        from django.db import close_old_connections

        # Just close any stale connections from parent process
        close_old_connections()

        # Seed in-memory gettext catalogs with DB-backed msgstrs. Without
        # this, a worker only sees the .po/.mo values baked into the image
        # and stays blind to every Rosetta save that lands post-deploy —
        # which is exactly what caused "Order Received - #38" to ship in
        # English even though the Greek msgstr was in the Translation
        # table on Postgres.
        try:
            from core.rosetta_storage import apply_db_overlay

            apply_db_overlay()
        except Exception as exc:
            logger.warning(
                "Could not apply translation overlay at worker init: %s", exc
            )

        logger.info("Worker process initialized")

    @task_prerun.connect
    def refresh_translations_if_bumped(**kwargs):
        """Re-apply the DB overlay when another pod bumped the version tick.

        Mirrors core.middleware.translation_reload for the worker side.
        Each task pays one cache.get() — a few bytes over Redis, already
        hot per worker process — to see if a Rosetta save has happened
        since the last check. No rebuild if unchanged.
        """
        global _worker_translation_version

        from django.core.cache import cache

        from core.rosetta_storage import (
            TRANSLATION_VERSION_CACHE_KEY,
            _reload_translations,
            apply_db_overlay,
        )

        try:
            remote_version = cache.get(TRANSLATION_VERSION_CACHE_KEY)
        except Exception:
            return

        if remote_version is None:
            return

        if _worker_translation_version == remote_version:
            return

        try:
            apply_db_overlay()
            _reload_translations()
        except Exception:
            logger.exception(
                "Failed to refresh worker translations after tick %s",
                remote_version,
            )
            return

        _worker_translation_version = remote_version
        logger.info(
            "Worker refreshed translations from DB (version %s)",
            remote_version,
        )

    @worker_process_shutdown.connect
    def shutdown_worker_process(**kwargs):
        """Clean up when worker shuts down."""
        from django.db import close_old_connections

        close_old_connections()
        logger.info("Worker process shutting down")

    return tasker


celery_app = create_celery_app()
