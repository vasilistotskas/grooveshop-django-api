import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from celery import Task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import management
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.db import connections, models, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from cart.models import Cart
from core import celery_app

User = get_user_model()

logger = logging.getLogger(__name__)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class MonitoredTask(Task):
    def on_success(self, retval, task_id, args, kwargs):
        logger.info(
            f"Task {self.name} completed successfully. Task ID: {task_id}"
        )

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            f"Task {self.name} failed. Task ID: {task_id}, Error: {exc}"
        )


def track_task_metrics(task_func):
    @wraps(task_func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        task_name = task_func.__name__

        try:
            result = task_func(*args, **kwargs)
            duration = time.time() - start_time

            logger.info(
                f"Task {task_name} completed successfully",
                extra={
                    "task_name": task_name,
                    "duration": duration,
                    "status": "success",
                },
            )

            return result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Task {task_name} failed",
                extra={
                    "task_name": task_name,
                    "duration": duration,
                    "status": "failure",
                    "error": str(e),
                },
            )
            raise

    return wrapper


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
@track_task_metrics
def clear_expired_sessions_task():
    try:
        logger.info("Starting expired sessions cleanup")
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
@track_task_metrics
def clear_all_cache_task():
    try:
        logger.info("Starting cache cleanup")
        management.call_command("clear_cache", verbosity=0)

        return {"status": "success", "message": "All cache deleted"}
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
@track_task_metrics
def clear_duplicate_history_task(excluded_fields=None, minutes=None):
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

        command_args.append("--auto")
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


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
@track_task_metrics
def clear_old_history_task(days=365):
    try:
        logger.info(
            f"Starting old history cleanup for entries older than {days} days"
        )

        command_args = ["clean_old_history"]

        if days is not None:
            command_args.extend(["--days", str(days)])

        command_args.append("--auto")
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
@track_task_metrics
def clear_expired_notifications_task(days=365):
    try:
        logger.info("Starting expired notifications cleanup")

        command_args = ["expire_notifications"]

        if days is not None:
            command_args.extend(["--days", str(days)])

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
@track_task_metrics
def clear_carts_for_none_users_task():
    try:
        with transaction.atomic():
            stats = Cart.objects.filter(user=None).aggregate(
                total_carts=models.Count("id"),
                total_items=models.Count("items"),
            )

            deleted_count, details = Cart.objects.filter(user=None).delete()

            message = (
                f"Cleared {stats['total_carts'] or 0} null carts and "
                f"{stats['total_items'] or 0} related cart items"
            )
            logger.info(message, extra={"deleted_details": details})

            return {
                "status": "success",
                "message": message,
                "carts_deleted": stats["total_carts"] or 0,
                "items_deleted": stats["total_items"] or 0,
                "details": details,
            }

    except Exception:
        logger.exception("Error clearing null user carts")
        raise


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
@track_task_metrics
def clear_log_files_task(days=30):
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

    message = f"Deleted {len(deleted_files)} log files older than {days} days"
    logger.info(
        message,
        extra={
            "deleted_count": len(deleted_files),
            "total_size": sum(f["size"] for f in deleted_files),
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
@track_task_metrics
def send_inactive_user_notifications():
    cutoff_date = timezone.now() - timedelta(days=60)

    inactive_users = User.objects.filter(
        last_login__lt=cutoff_date,
        is_active=True,
        email__isnull=False,
        email__gt="",
    ).values_list("id", "email", "first_name", "username")

    total_users = inactive_users.count()
    success_count = 0
    failed_emails = []

    logger.info(f"Starting to send emails to {total_users} inactive users")

    for user_id, email, first_name, username in inactive_users.iterator(
        chunk_size=100
    ):
        try:
            mail_subject = _("We miss you!")

            context = {
                "user_id": user_id,
                "first_name": first_name or username,
                "app_base_url": settings.NUXT_BASE_URL,
            }

            message = render_to_string(
                "emails/inactive_user_email_template.html", context
            )

            send_mail(
                subject=mail_subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
                html_message=message,
            )

            success_count += 1

            if success_count % 100 == 0:
                logger.info(f"Sent {success_count} emails so far...")

        except Exception as e:
            logger.error(
                f"Failed to send email to user {user_id}",
                extra={"user_id": user_id, "error": str(e)},
            )
            failed_emails.append(
                {
                    "user_id": user_id,
                    "email": email[:3] + "***",
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
@track_task_metrics
def monitor_system_health():
    health_checks = {"database": False, "cache": False, "storage": False}
    errors = []

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

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
        logger.error(error_msg)
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
@track_task_metrics
def backup_database_task(
    output_dir="backups", filename=None, compress=False, format_type="custom"
):
    try:
        logger.info(
            "Starting database backup",
            extra={
                "output_dir": output_dir,
                "filename": filename,
                "compress": compress,
                "format": format_type,
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
                "backup_file": str(backup_file) if backup_file else None,
            },
        )

        return {
            "status": "success",
            "message": success_message,
            "duration": duration,
            "file_size": file_size,
            "backup_file": str(backup_file) if backup_file else None,
            "timestamp": start_time.isoformat(),
            "parameters": {
                "output_dir": output_dir,
                "filename": filename,
                "compress": compress,
                "format": format_type,
            },
        }

    except management.CommandError as e:
        logger.error(f"Django command error in backup_database: {e}")
        return {
            "status": "error",
            "message": str(e),
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
@track_task_metrics
def scheduled_database_backup():
    try:
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"scheduled_backup_{timestamp}"

        logger.info("Starting scheduled database backup")

        result = backup_database_task.apply(
            kwargs={
                "output_dir": "backups/scheduled",
                "filename": filename,
                "compress": True,
                "format_type": "custom",
            }
        )

        if result.result.get("status") == "success":
            logger.info(
                "Scheduled database backup completed successfully",
                extra=result.result,
            )

            cleanup_old_backups.delay(days=7, backup_dir="backups/scheduled")

            return result.result
        else:
            raise Exception(
                f"Backup failed: {result.result.get('message', 'Unknown error')}"
            )

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
@track_task_metrics
def cleanup_old_backups(days=30, backup_dir="backups"):
    try:
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


try:
    validate_task_configuration()
except ImproperlyConfigured as e:
    logger.error(f"Task configuration error: {e}")
