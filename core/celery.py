import logging
import os

from celery import Celery
from celery.signals import setup_logging, worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

logger = logging.getLogger(__name__)


@setup_logging.connect
def config_loggers(*args, **kwags):
    from django.conf import settings  # noqa: F401, PLC0415


def create_celery_app():
    tasker = Celery("core")
    tasker.conf.enable_utc = False
    tasker.conf.update(timezone=os.getenv("TIME_ZONE", "Europe/Athens"))

    tasker.conf.update(
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

    tasker.config_from_object("django.conf:settings", namespace="CELERY")
    tasker.autodiscover_tasks()

    # Minimal connection management - let Django handle most of it
    from celery.signals import worker_process_shutdown

    @worker_process_init.connect
    def init_worker_process(**kwargs):
        """Initialize worker process with clean connections."""
        from django.db import close_old_connections

        # Just close any stale connections from parent process
        close_old_connections()
        logger.info("Worker process initialized")

    @worker_process_shutdown.connect
    def shutdown_worker_process(**kwargs):
        """Clean up when worker shuts down."""
        from django.db import close_old_connections

        close_old_connections()
        logger.info("Worker process shutting down")

    return tasker


celery_app = create_celery_app()
