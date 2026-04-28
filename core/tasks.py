import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from celery import Task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import management
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import connections, transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

# See order/tasks.py for rationale — eager gettext over lazy for email subjects.
from django.utils import translation
from django.utils.translation import gettext as _

from core.utils.i18n import get_user_language

from extra_settings.models import Setting

from cart.models import Cart
from core import celery_app

User = get_user_model()

logger = logging.getLogger(__name__)


_SECRET_KEYS = frozenset(
    {"password", "token", "key", "secret", "auth", "credential", "passwd"}
)
_ARG_TRUNCATE = 500


def _safe_repr(value: Any, key: str | None = None) -> str:
    """Return a truncated repr of *value*, masking secret-looking keys."""
    if key is not None:
        lower = str(key).lower()
        if any(sk in lower for sk in _SECRET_KEYS):
            return "***"
    text = repr(value)
    if len(text) > _ARG_TRUNCATE:
        return text[:_ARG_TRUNCATE] + "…"
    return text


class MonitoredTask(Task):
    def on_success(self, retval, task_id, args, kwargs):
        logger.info(
            f"Task {self.name} completed successfully. Task ID: {task_id}"
        )

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        safe_args = [_safe_repr(a) for a in (args or [])]
        safe_kwargs = {
            k: _safe_repr(v, key=k) for k, v in (kwargs or {}).items()
        }
        logger.error(
            "Task %s failed. Task ID: %s, Error: %s, args=%s, kwargs=%s",
            self.name,
            task_id,
            exc,
            safe_args,
            safe_kwargs,
        )
        if einfo is not None:
            logger.debug(
                "Task %s traceback:\n%s",
                self.name,
                einfo.traceback,
            )


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def clear_expired_sessions_task():
    try:
        logger.info("Starting expired sessions cleanup")

        with transaction.atomic():
            management.call_command("clearsessions", verbosity=0)

        return {"status": "success", "message": "All expired sessions deleted"}
    except management.CommandError as e:
        logger.error(f"Django command error in clear_expired_sessions: {e}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in clear_expired_sessions")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def clear_all_cache_task():
    try:
        logger.info("Starting cache cleanup")
        management.call_command("clear_cache", verbosity=0)

        return {"status": "success", "message": "Cache cleared by prefix"}
    except management.CommandError as e:
        logger.error(f"Django command error in clear_cache: {e}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in clear_cache")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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

        command_args = ["clean_duplicate_history"]

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

    except management.CommandError as e:
        logger.error(f"Django command error in clear_duplicate_history: {e}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in clear_duplicate_history")
        raise
    finally:
        cache.delete(lock_id)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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

    except management.CommandError as e:
        logger.error(f"Django command error in clear_old_history: {e}")
        return {
            "status": "error",
            "message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in clear_old_history")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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

    except management.CommandError as e:
        logger.error(
            f"Django command error in clear_expired_notifications: {e}"
        )
        return {
            "status": "error",
            "message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in clear_expired_notifications")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def cleanup_abandoned_carts():
    try:
        days = Setting.get("ABANDONED_CART_CLEANUP_DAYS", default=7)
        cutoff_date = timezone.now() - timedelta(days=days)

        with transaction.atomic():
            count, _ = Cart.objects.filter(
                user__isnull=True,
                last_activity__lt=cutoff_date,
                items__isnull=True,
            ).delete()

        message = (
            f"Cleaned up {count} abandoned guest carts"
            if count > 0
            else "No abandoned carts to clean up"
        )
        logger.info(message)

        return {
            "status": "success",
            "message": message,
            "deleted_count": count,
            "cutoff_date": cutoff_date.isoformat(),
            "timestamp": timezone.now().isoformat(),
        }
    except Exception:
        logger.exception("Error cleaning up abandoned carts")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def cleanup_old_guest_carts():
    try:
        days = Setting.get("OLD_GUEST_CART_CLEANUP_DAYS", default=30)
        cutoff_date = timezone.now() - timedelta(days=days)

        count, _ = Cart.objects.filter(
            user__isnull=True,
            last_activity__lt=cutoff_date,
        ).delete()

        message = (
            f"Cleaned up {count} old guest carts ({days}+ days inactive)"
            if count > 0
            else "No old guest carts to clean up"
        )
        logger.info(message)

        return {
            "status": "success",
            "message": message,
            "deleted_count": count,
            "cutoff_date": cutoff_date.isoformat(),
            "timestamp": timezone.now().isoformat(),
        }
    except Exception:
        logger.exception("Error cleaning up old guest carts")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    autoretry_for=(Exception,),
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

    deleted_files = []
    errors = []
    cutoff_date = timezone.now() - timedelta(days=days)

    try:
        for filename in os.listdir(logs_path):
            file_path = os.path.join(logs_path, filename)

            if not os.path.isfile(file_path):
                continue

            should_cleanup = (
                filename.endswith(".log")
                or ".log." in filename
                or "backup" in filename.lower()
                or filename.endswith(".bak")
                or filename.endswith(".old")
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
                        {
                            "filename": filename,
                            "size": file_size,
                            "modified": file_date.isoformat(),
                        }
                    )

            except OSError as e:
                logger.error(f"Error processing file {filename}: {e}")
                errors.append({"file": filename, "error": str(e)})

    except OSError as e:
        logger.exception(f"Error accessing logs directory: {e}")
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
    soft_time_limit=600,
    time_limit=900,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_inactive_user_notifications() -> dict[str, Any]:
    """
    Send re-engagement emails to inactive users.

    Limits to MAX_REENGAGEMENT_EMAILS per user with a 90-day cooldown
    between sends.  Uses iterator() for memory-efficient processing.

    Returns:
        Dictionary with task execution statistics
    """
    now = timezone.now()
    max_emails = Setting.get("REENGAGEMENT_EMAIL_MAX_COUNT", default=3)
    cooldown_days = Setting.get("REENGAGEMENT_EMAIL_COOLDOWN_DAYS", default=90)
    inactive_days = Setting.get("INACTIVE_USER_THRESHOLD_DAYS", default=60)
    cutoff_date = now - timedelta(days=inactive_days)
    cooldown_cutoff = now - timedelta(days=cooldown_days)

    inactive_users_qs = (
        User.objects.filter(
            last_login__lt=cutoff_date,
            is_active=True,
            email__isnull=False,
            email__gt="",
            reengagement_email_count__lt=max_emails,
        )
        .exclude(
            last_reengagement_email_at__gt=cooldown_cutoff,
        )
        .only("id", "email", "first_name", "username")
    )

    success_count = 0
    failed_emails: list[dict[str, Any]] = []
    total_users = 0

    logger.info("Starting to send emails to inactive users")

    for user in inactive_users_qs.iterator(chunk_size=200):
        total_users += 1

        try:
            from user.utils.subscription import (
                build_list_unsubscribe_headers,
                generate_blanket_unsubscribe_link,
            )

            unsubscribe_url = generate_blanket_unsubscribe_link(user)

            context = {
                "user": {
                    "id": user.id,
                    "first_name": user.first_name,
                    "username": user.username,
                    "email": user.email,
                },
                "unsubscribe_url": unsubscribe_url,
                "SITE_NAME": settings.SITE_NAME,
                "INFO_EMAIL": settings.INFO_EMAIL,
                "SITE_URL": settings.NUXT_BASE_URL,
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }

            with translation.override(get_user_language(user)):
                mail_subject = _("We miss you!")
                html_body = render_to_string(
                    "emails/user/inactive_user_email_template.html", context
                )
                text_body = render_to_string(
                    "emails/user/inactive_user_email_template.txt", context
                )

            email_msg = EmailMultiAlternatives(
                subject=mail_subject,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
                reply_to=[settings.INFO_EMAIL],
                headers=build_list_unsubscribe_headers(
                    unsubscribe_url, list_id="reengagement"
                ),
            )
            email_msg.attach_alternative(html_body, "text/html")
            email_msg.send(fail_silently=False)

            User.objects.filter(pk=user.pk).update(
                reengagement_email_count=F("reengagement_email_count") + 1,
                last_reengagement_email_at=now,
            )

            success_count += 1

            if success_count % 100 == 0:
                logger.info(f"Sent {success_count} emails so far...")

        except Exception as e:
            logger.error(
                f"Failed to send email to user {user.pk}",
                extra={"user_id": user.pk, "error": str(e)},
            )
            failed_emails.append(
                {
                    "user_id": user.pk,
                    "email": (user.email or "")[:3] + "***",
                    "error": str(e),
                }
            )

            if len(failed_emails) > 50:
                logger.error("Too many email failures, stopping task")
                break

    message = f"Sent {success_count} re-engagement emails to inactive users"
    logger.info(
        message,
        extra={
            "total_users": total_users,
            "emails_sent": success_count,
            "failed_count": len(failed_emails),
        },
    )

    return {
        "status": "completed",
        "message": message,
        "total_users": total_users,
        "emails_sent": success_count,
        "failed": len(failed_emails),
        "failed_details": failed_emails[:10],
    }


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def monitor_system_health():
    health_checks = {"database": False, "cache": False, "storage": False}
    errors = []

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            if result:
                health_checks["database"] = True
                logger.debug("Database health check passed")

    except Exception as e:
        error_msg = f"Database health check failed: {e}"
        logger.error(error_msg)
        errors.append(error_msg)

    try:
        cache.set("health_check", "ok", 30)
        if cache.get("health_check") == "ok":
            health_checks["cache"] = True
            logger.debug("Cache health check passed")
        else:
            raise Exception("Cache read/write test failed")

    except Exception as e:
        error_msg = f"Cache health check failed: {e}"
        logger.warning(error_msg)
        errors.append(error_msg)

    try:
        test_file_path = os.path.join(settings.MEDIA_ROOT, ".health_check")
        with open(test_file_path, "w") as f:
            f.write("ok")
        os.remove(test_file_path)
        health_checks["storage"] = True
        logger.debug("Storage health check passed")

    except Exception as e:
        error_msg = f"Storage health check failed: {e}"
        logger.warning(error_msg)
        errors.append(error_msg)

    all_passed = all(health_checks.values())
    critical_passed = health_checks["database"]

    if not critical_passed:
        try:
            send_mail(
                subject="CRITICAL: System Health Check Failed",
                message="Critical system components have failed health checks:\n\n"
                + "\n".join(errors),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.ADMIN_EMAIL],
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"Failed to send health check alert email: {e}")

    result = {
        "status": "healthy"
        if all_passed
        else ("degraded" if critical_passed else "unhealthy"),
        "timestamp": timezone.now().isoformat(),
        "checks": health_checks,
        "errors": errors,
    }

    logger.info("System health check completed", extra=result)

    if not critical_passed:
        raise Exception("Critical system health check failed")

    return result


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    soft_time_limit=1800,
    time_limit=2400,
    autoretry_for=(Exception,),
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
                backup_file = max(backup_files, key=lambda f: f.stat().st_ctime)
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

    except management.CommandError as e:
        logger.error(f"Django command error in backup_database: {e}")
        return {
            "status": "error",
            "result_message": str(e),
            "error_type": "CommandError",
        }
    except Exception:
        logger.exception("Unexpected error in backup_database")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def scheduled_database_backup():
    try:
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"scheduled_backup_{timestamp}"

        logger.info("Starting scheduled database backup")

        # Use Celery chain to avoid blocking the worker with .get()
        from celery import chain

        chain(
            backup_database_task.s(
                output_dir="backups/scheduled",
                filename=filename,
                compress=True,
                format_type="custom",
            ),
            cleanup_old_backups.si(days=7, backup_dir="backups/scheduled"),
        ).apply_async()

        logger.info("Scheduled database backup chain dispatched")
        return {
            "status": "dispatched",
            "message": "Backup task chain started",
            "filename": filename,
        }

    except Exception as e:
        logger.error(f"Scheduled backup failed: {e}")

        try:
            send_mail(
                subject="ALERT: Scheduled Database Backup Failed",
                message=f"The scheduled database backup failed with error: {e!s}\n\n"
                f"Please check the system logs and ensure the backup system is working properly.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.ADMIN_EMAIL],
                fail_silently=True,
            )
        except Exception as mail_error:
            logger.error(
                f"Failed to send backup failure alert email: {mail_error}"
            )

        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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
        "ADMIN_EMAIL",
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


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def sync_meilisearch_indexes():
    """
    Sync all Meilisearch indexes by calling the management command.
    Scheduled to run daily at 2 AM.
    """
    logger.info("Starting Meilisearch index synchronization")

    try:
        management.call_command("meilisearch_sync_all_indexes")
        logger.info("Meilisearch index synchronization completed successfully")
        return {
            "status": "success",
            "result_message": "All Meilisearch indexes synchronized successfully",
            "timestamp": timezone.now().isoformat(),
        }
    except SystemExit as e:
        logger.error(f"Meilisearch sync command exited with code: {e.code}")
        raise Exception(f"Command exited with code {e.code}")
    except management.CommandError as e:
        logger.error(f"Django command error in sync_meilisearch_indexes: {e}")
        raise
    except Exception:
        logger.exception("Unexpected error in sync_meilisearch_indexes")
        raise
