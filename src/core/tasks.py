import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from django.conf import settings
from django.core import management
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core import celery_app
from core.cache import edge as edge_cache
from core.cache import invalidation as cache_invalidation
from core.cache.edge import EdgePurgeError
from core.exceptions import (
    StorePagesPurgeFailed,
)
from core.monitored_task import MonitoredTask
from core.retention import (
    delete_in_batches,
    purge_expired_auth_tokens,
    purge_orphaned_user_sessions,
)
from core.retry import (
    TRANSIENT_DB_ERRORS,
    TRANSIENT_ERRORS,
)
from core.store_settings import store_setting

logger = logging.getLogger(__name__)


class _DeletedLogFile(TypedDict):
    """One pruned development log file, as reported in the task's `extra`."""

    filename: str
    size: int
    modified: str


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def clear_expired_sessions_task():
    try:
        logger.info("Starting expired sessions cleanup")

        with transaction.atomic():
            management.call_command("clearsessions", verbosity=0)
            # By state, after ``clearsessions`` made the session table the
            # truth: the rows allauth and knox would only clean when the
            # same user came back.
            orphaned_user_sessions = purge_orphaned_user_sessions()
            expired_tokens = purge_expired_auth_tokens()

        return {
            "status": "success",
            "message": "All expired sessions deleted",
            "orphaned_user_sessions": orphaned_user_sessions,
            "expired_tokens": expired_tokens,
        }
    except Exception:
        logger.exception("Unexpected error in clear_expired_sessions")
        raise


@celery_app.task(base=MonitoredTask)
def invalidate_cache_surfaces_task():
    """Purge the cache surfaces the current schema's recent commits made stale.

    Queued by ``core.cache.invalidation`` after a write to a model a surface
    declares in ``invalidated_by``; runs in that schema (``TenantTask``).
    No retry: ``CacheService.purge`` isolates each surface's failures and
    logs them, and a retry would purge surfaces a later commit already
    re-queued.
    """
    surfaces = cache_invalidation.run_queued_purge()
    return {"status": "success", "surfaces": surfaces}


@celery_app.task(base=MonitoredTask)
def release_due_publications_task():
    """Act on the current store's rows whose publish date just passed.

    Nothing is saved when ``published_at`` passes, so no signal fires;
    this beat task is what notices (``core.publication``). No retry: the
    cursor moves only after a run succeeds, so the next minute's run
    repeats the window.
    """
    from core.publication import release_due_publications

    return {"released": release_due_publications()}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(StorePagesPurgeFailed,),
    retry_backoff=True,
    retry_jitter=True,
)
def purge_store_pages_task():
    """Purge every page the storefront has cached for the current store.

    Queued by ``tenant.signals`` when one of the store's feature switches
    really changes; runs in that store's schema (``TenantTask``).

    Retried while the storefront's purge endpoint fails (unreachable, an
    error answer): one switch queues one purge, so nothing later would
    make up for it, and the store would keep showing the feature until
    each page's copy expired. A purge is idempotent, so a retry is safe.
    An endpoint that is not configured is reported by ``CacheService``
    and not retried, since nothing would change.
    """
    # Imported here: ``CacheService`` reaches the cache backend and the
    # Nuxt client at import time.
    from core.cache import nuxt as nuxt_client
    from core.cache.service import CacheService
    from core.cache.surfaces import STORE_PAGES_SURFACE

    report = CacheService.purge([STORE_PAGES_SURFACE], include_related=False)
    failed = [s.code for s in report.failed_surfaces]
    if failed and nuxt_client.is_configured():
        raise StorePagesPurgeFailed(failed)
    return {"status": "success", "nuxt": report.nuxt_headline}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(StorePagesPurgeFailed,),
    retry_backoff=True,
    retry_jitter=True,
)
def purge_store_locales_task():
    """Purge what the storefront cached for the current store's languages.

    Queued by ``tenant.signals`` when ``Tenant.available_locales`` really
    changes; runs in that store's schema (``TenantTask``). Every page
    shows the language switcher and its hreflang set, and the sitemap
    lists each language's URLs, so both surfaces go. Retried like
    ``purge_store_pages_task``: one change queues one purge.
    """
    from core.cache import nuxt as nuxt_client
    from core.cache.service import CacheService
    from core.cache.surfaces import SITEMAP_SEO_SURFACE, STORE_PAGES_SURFACE

    report = CacheService.purge(
        [STORE_PAGES_SURFACE, SITEMAP_SEO_SURFACE], include_related=False
    )
    failed = [s.code for s in report.failed_surfaces]
    if failed and nuxt_client.is_configured():
        raise StorePagesPurgeFailed(failed)
    return {"status": "success", "nuxt": report.nuxt_headline}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(EdgePurgeError,),
    # Cloudflare's Free plan refills five purges a minute; back off past it.
    retry_backoff=15,
    retry_jitter=True,
)
def purge_edge_cache_task():
    """Purge storefront pages from Cloudflare after Nitro's were purged.

    Queued by ``core.cache.edge.schedule_purge``; runs in the schema that
    queued it (``TenantTask``): one store's pages, or every store's.
    """
    zones = edge_cache.run_scheduled_purge()
    return {"status": "success", "zones": zones}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def clear_all_cache_task():
    try:
        logger.info("Starting cache cleanup")
        # ``--all`` is REQUIRED: with no surfaces and no --all the command
        # falls through to its "list available surfaces" branch and purges
        # nothing, while this task still reported success.
        #
        # SCOPE: this clears the PUBLIC schema only. Beat fires in public
        # and surface patterns are built through ``make_key``, so
        # ``CustomCache._make_pattern`` embeds the ACTIVE schema and the
        # SCAN never leaves it (measured: from ``demo``,
        # ``cache.keys("*ViewSet_*")`` returns only ``demo:`` keys). A
        # comment here used to claim the opposite — that the wildcards
        # bypass the schema prefix and so already span every tenant — and
        # it was wrong in the direction that matters.
        #
        # Tenant caches are therefore NOT cleared by this job; they expire
        # on DEFAULT_CACHE_TTL, and targeted invalidation goes through
        # CacheService from the admin. Making this a fanout would start
        # wiping every store's cache on a schedule, which is a behaviour
        # change to decide deliberately rather than infer from the name.
        management.call_command("clear_cache", "--all", verbosity=0)

        return {"status": "success", "message": "Cache surfaces purged"}
    except Exception:
        logger.exception("Unexpected error in clear_cache")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def clear_duplicate_history_task(excluded_fields=None, minutes=None):
    # Use cache-based lock to prevent concurrent execution
    lock_id = "clear_duplicate_history_lock"
    lock_timeout = 300  # 5 minutes

    # Try to acquire lock
    lock_acquired = cache.add(lock_id, "locked", lock_timeout)

    if not lock_acquired:
        logger.warning(
            "Another instance of clear_duplicate_history is already running, skipping"
        )
        return {
            "status": "skipped",
            "message": "Another instance is already running",
        }

    try:
        logger.info(
            "Starting duplicate history cleanup",
            extra={"excluded_fields": excluded_fields, "minutes": minutes},
        )

        # ``--auto``, like the sibling ``clear_old_history_task``.
        # Without a model name or this flag, simple-history's command
        # falls through to printing "Please specify a model or use the
        # --auto option" and does nothing — measured: 0 queries against
        # 4 with the flag — while this task went on to log "Successfully
        # cleaned duplicate history entries" and return success. The
        # daily per-tenant beat job has therefore never removed a
        # duplicate historical row, in any tenant.
        command_args = ["clean_duplicate_history", "--auto"]

        if minutes is not None:
            command_args.extend(["-m", str(minutes)])
        if excluded_fields:
            command_args.extend(["--excluded_fields", *excluded_fields])

        with transaction.atomic():
            management.call_command(*command_args)

        logger.info("Successfully cleaned duplicate history entries")
        return {
            "status": "success",
            "message": "Duplicate history entries cleaned",
            "parameters": {
                "excluded_fields": excluded_fields,
                "minutes": minutes,
            },
        }

    except Exception:
        logger.exception("Unexpected error in clear_duplicate_history")
        raise
    finally:
        cache.delete(lock_id)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def clear_old_history_task(days=365):
    try:
        logger.info(
            f"Starting old history cleanup for entries older than {days} days"
        )

        command_args = ["clean_old_history"]
        if days is not None:
            command_args.extend(["--days", str(days)])
        command_args.append("--auto")

        with transaction.atomic():
            management.call_command(*command_args)

        logger.info(
            f"Successfully cleaned history entries older than {days} days"
        )
        return {
            "status": "success",
            "message": f"Old history entries cleaned (older than {days} days)",
        }

    except Exception:
        logger.exception("Unexpected error in clear_old_history")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def purge_expired_admin_log_entries() -> dict[str, int]:
    """Delete ``django_admin_log`` rows older than ``ADMIN_LOG_RETENTION_DAYS``.

    The staff audit trail of the ACTIVE schema: a store's through
    ``tenant.tasks.fanout_purge_expired_admin_log_entries`` with that
    store's setting, the platform's own in public with the setting
    stored there. ``0`` keeps every row.
    """
    from django.contrib.admin.models import LogEntry

    days = int(store_setting("ADMIN_LOG_RETENTION_DAYS"))
    if days <= 0:
        return {"deleted": 0}
    cutoff = timezone.now() - timedelta(days=days)
    deleted = delete_in_batches(LogEntry.objects.filter(action_time__lt=cutoff))
    if deleted:
        logger.info(
            "Purged %s admin log entries older than %s days", deleted, days
        )
    return {"deleted": deleted}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def purge_expired_cache_purge_logs() -> dict[str, int]:
    """Delete ``CachePurgeLog`` rows older than the retention period.

    ``CACHE_PURGE_LOG_RETENTION_DAYS``. The table lives in the public
    schema only and holds every store's purges (tagged by
    ``schema_name``), so this runs once from beat, not per tenant.
    """
    from core.cache.models import CachePurgeLog

    cutoff = timezone.now() - timedelta(
        days=settings.CACHE_PURGE_LOG_RETENTION_DAYS
    )
    deleted = delete_in_batches(
        CachePurgeLog.objects.filter(created_at__lt=cutoff)
    )
    if deleted:
        logger.info("Purged %s cache purge log rows", deleted)
    return {"deleted": deleted}


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def clear_expired_notifications_task(days=365):
    try:
        logger.info("Starting expired notifications cleanup")

        command_args = ["expire_notifications"]

        if days is not None:
            command_args.extend(["--days", str(days)])

        with transaction.atomic():
            management.call_command(*command_args)

        return {"status": "success", "message": "Expired notifications deleted"}

    except Exception:
        logger.exception("Unexpected error in clear_expired_notifications")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def clear_development_log_files_task(days=7):
    """
    Clean up log files in development Docker environment only.
    This task only runs in Docker development, not in Kubernetes.
    """
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        logger.info("Skipping log cleanup - running in Kubernetes")
        return {
            "status": "skipped",
            "reason": "kubernetes environment detected",
        }

    if not os.path.exists("/.dockerenv"):
        logger.info("Skipping log cleanup - not in Docker environment")
        return {"status": "skipped", "reason": "not in docker environment"}

    logs_path = os.path.join(settings.BASE_DIR, "logs")

    if not os.path.exists(logs_path):
        logger.warning(f"Logs directory does not exist: {logs_path}")
        return {
            "status": "skipped",
            "reason": "logs directory not found",
            "path": logs_path,
        }

    # Typed record, not a bare dict literal: `{"filename": str, "size":
    # int, ...}` infers a value type of `str | int`, so `sum(f["size"]
    # for f in deleted_files)` cannot be shown to be summing numbers.
    deleted_files: list[_DeletedLogFile] = []
    errors = []
    cutoff_date = timezone.now() - timedelta(days=days)

    try:
        for filename in os.listdir(logs_path):
            file_path = os.path.join(logs_path, filename)

            if not os.path.isfile(file_path):
                continue

            should_cleanup = (
                filename.endswith((".log", ".bak", ".old"))
                or ".log." in filename
                or "backup" in filename.lower()
            )

            if not should_cleanup:
                continue

            try:
                file_mtime = os.path.getmtime(file_path)
                file_date = datetime.fromtimestamp(
                    file_mtime, tz=timezone.get_current_timezone()
                )

                if file_date < cutoff_date:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    deleted_files.append(
                        _DeletedLogFile(
                            filename=filename,
                            size=file_size,
                            modified=file_date.isoformat(),
                        )
                    )

            except OSError as e:
                logger.error(f"Error processing file {filename}: {e}")
                errors.append({"file": filename, "error": str(e)})

    except OSError:
        logger.exception("Error accessing logs directory")
        raise

    message = f"Deleted {len(deleted_files)} development log files older than {days} days"
    logger.info(
        message,
        extra={
            "deleted_count": len(deleted_files),
            "total_size": sum(f["size"] for f in deleted_files),
            "environment": "docker_development",
        },
    )

    return {
        "status": "success",
        "message": message,
        "deleted_count": len(deleted_files),
        "deleted_files": deleted_files[:20],
        "errors": errors,
    }


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    soft_time_limit=1800,
    time_limit=2400,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def backup_database_task(
    output_dir="backups", filename=None, compress=False, format_type="custom"
):
    try:
        logger.info(
            "Starting database backup",
            extra={
                "output_dir": output_dir,
                "backup_filename": filename,
                "compress": compress,
                "backup_format": format_type,
            },
        )

        command_args = ["backup_database"]

        if output_dir:
            command_args.extend(["--output-dir", output_dir])

        if filename:
            command_args.extend(["--filename", filename])

        if compress:
            command_args.append("--compress")

        if format_type and format_type in ["custom", "plain", "tar"]:
            command_args.extend(["--format", format_type])

        start_time = timezone.now()

        management.call_command(*command_args)

        duration = (timezone.now() - start_time).total_seconds()

        backup_dir = Path(settings.BASE_DIR) / output_dir

        backup_file = None
        file_size = 0

        if backup_dir.exists():
            backup_files = [
                f
                for f in backup_dir.iterdir()
                if f.is_file()
                and (
                    f.name.startswith("backup_")
                    or (filename and f.name.startswith(filename))
                )
            ]

            if backup_files:
                backup_file = max(backup_files, key=lambda f: f.stat().st_mtime)
                file_size = backup_file.stat().st_size

        success_message = (
            f"Database backup completed successfully in {duration:.2f} seconds"
        )

        if backup_file:
            success_message += (
                f". File: {backup_file.name}, Size: {file_size:,} bytes"
            )

        logger.info(
            success_message,
            extra={
                "duration": duration,
                "file_size": file_size,
                "backup_file_path": str(backup_file) if backup_file else None,
            },
        )

        return {
            "status": "success",
            "result_message": success_message,
            "duration": duration,
            "file_size": file_size,
            "backup_file": str(backup_file) if backup_file else None,
            "timestamp": start_time.isoformat(),
            "parameters": {
                "output_dir": output_dir,
                "backup_filename": filename,
                "compress": compress,
                "backup_format": format_type,
            },
        }

    except Exception:
        # A failed dump has to surface as a FAILED task. Catching
        # CommandError here and returning ``{"status": "error"}`` made
        # Celery record SUCCESS, MonitoredTask.on_success log "completed
        # successfully", the autoretry never fire, and the chained
        # cleanup_old_backups keep pruning the retention window — so a
        # pg_dump/server major mismatch went unnoticed for a week while
        # the backup PVC drained to zero (2026-09-02).
        logger.exception("Database backup failed")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def cleanup_old_backups(days=30, backup_dir="backups"):
    try:
        # Handle both relative and absolute paths
        if Path(backup_dir).is_absolute():
            backup_path = Path(backup_dir)
        else:
            backup_path = Path(settings.BASE_DIR) / backup_dir

        if not backup_path.exists():
            logger.warning(f"Backup directory does not exist: {backup_path}")
            return {
                "status": "skipped",
                "reason": "backup directory not found",
                "path": str(backup_path),
            }

        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_files = []
        total_size_freed = 0

        logger.info(
            f"Cleaning up backup files older than {days} days in {backup_path}"
        )

        for backup_file in backup_path.iterdir():
            if not backup_file.is_file():
                continue

            try:
                file_mtime = backup_file.stat().st_mtime
                file_date = datetime.fromtimestamp(
                    file_mtime, tz=timezone.get_current_timezone()
                )

                if file_date < cutoff_date:
                    file_size = backup_file.stat().st_size
                    backup_file.unlink()

                    deleted_files.append(
                        {
                            "filename": backup_file.name,
                            "size": file_size,
                            "modified": file_date.isoformat(),
                        }
                    )
                    total_size_freed += file_size

            except OSError as e:
                logger.error(
                    f"Error processing backup file {backup_file.name}: {e}"
                )

        message = f"Cleaned up {len(deleted_files)} old backup files, freed {total_size_freed:,} bytes"

        logger.info(
            message,
            extra={
                "deleted_count": len(deleted_files),
                "total_size_freed": total_size_freed,
                "backup_dir": str(backup_path),
            },
        )

        return {
            "status": "success",
            "message": message,
            "deleted_count": len(deleted_files),
            "total_size_freed": total_size_freed,
            "deleted_files": deleted_files[:10],
        }

    except Exception:
        logger.exception("Error during backup cleanup")
        raise


def validate_task_configuration():
    required_settings = [
        "DEFAULT_FROM_EMAIL",
        "ADMINS",
        "MEDIA_ROOT",
        "BASE_DIR",
    ]

    missing_settings = []
    for setting in required_settings:
        if not hasattr(settings, setting):
            missing_settings.append(setting)

    if missing_settings:
        raise ImproperlyConfigured(
            f"Missing required settings: {', '.join(missing_settings)}"
        )

    logger.info("Task configuration validated successfully")
