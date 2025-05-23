import datetime
import logging
import os
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import management
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.timezone import now

from core import celery_app

logger = logging.getLogger(__name__)

User = get_user_model()
languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


@celery_app.task
def clear_expired_sessions_task(self):
    try:
        management.call_command("clearsessions", verbosity=0)
        return "All expired sessions deleted."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_all_cache_task(self):
    try:
        management.call_command("clear_cache", verbosity=0)
        return "All cache deleted."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_duplicate_history_task(excluded_fields=None, minutes=None):
    try:
        command_args = ["clean_duplicate_history"]

        if minutes is not None:
            command_args.extend(["-m", str(minutes)])
        if excluded_fields:
            command_args.extend(["--excluded_fields", *excluded_fields])

        command_args.append("--auto")
        management.call_command(*command_args)
        return "Duplicate history entries cleaned."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_old_history_task(days=365):
    try:
        command_args = ["clean_old_history"]

        if days is not None:
            command_args.extend(["--days", str(days)])

        command_args.append("--auto")
        management.call_command(*command_args)
        return "Old history entries cleaned."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_expired_notifications_task(days=365):
    try:
        management.call_command("expire_notifications")
        return "Expired notifications deleted."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_carts_for_none_users_task(self):
    from cart.models import Cart

    with transaction.atomic():
        null_carts = Cart.objects.filter(user=None).prefetch_related("items")
        cart_items_count = sum(cart.items.count() for cart in null_carts)
        carts_count = null_carts.count()
        null_carts.delete()

        message = f"Cleared {carts_count} null carts and {cart_items_count} related cart items."
        logger.info(message)
        return message


@celery_app.task
def clear_log_files_task(days=30):
    from django.conf import settings

    logs_path = os.path.join(settings.BASE_DIR, "logs")
    files = os.listdir(logs_path)

    for file in files:
        file_path = os.path.join(logs_path, file)
        file_modification_date = datetime.datetime.fromtimestamp(
            os.path.getmtime(file_path), tz=datetime.UTC
        )
        if datetime.datetime.now(
            tz=datetime.UTC
        ) - file_modification_date > datetime.timedelta(days=days):
            os.remove(file_path)

    message = f"Removed log files older than {days} days."

    logger.info(message)
    return message


@celery_app.task
def clear_blacklisted_tokens_task(self):
    try:
        management.call_command("flushexpiredtokens", verbosity=0)
        return "All expired blacklisted tokens deleted."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def send_inactive_user_notifications():
    cutoff_date = now() - datetime.timedelta(days=30)
    inactive_users = User.objects.filter(last_login__lt=cutoff_date)

    for user in inactive_users:
        mail_subject = "We miss you!"
        message = render_to_string(
            "inactive_user_email_template.html",
            {
                "user": user,
                "app_base_url": settings.NUXT_BASE_URL,
            },
        )

        send_mail(
            subject=mail_subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
            html_message=message,
        )

    return (
        f"Sent re-engagement emails to {inactive_users.count()} inactive users."
    )


@celery_app.task
def monitor_system_health():
    try:
        from django.db import connections

        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    except Exception as e:
        logger.error(f"System health check failed: {e}")

        send_mail(
            subject="System Health Check Alert",
            message=f"An error occurred during system health checks: {e}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.ADMIN_EMAIL],
            fail_silently=False,
        )
        return "System health check failed."

    return "System health check passed."


@celery_app.task
def backup_database():
    start_time = time.time()
    try:
        management.call_command("dbbackup", verbosity=3)
        end_time = time.time()
        execution_time = end_time - start_time
        return f"Database backup completed successfully in {execution_time:.2f} seconds."
    except Exception as e:
        return f"error: {e}"


@celery_app.task
def clear_old_database_backups(days=30):
    backups_path = os.path.join(settings.BASE_DIR, "backups")
    if not os.path.exists(backups_path):
        return "Backups directory does not exist."

    deleted_files_count = 0
    folder = os.listdir(backups_path)

    for filename in folder:
        if not filename.endswith(".psql.bin"):
            continue

        try:
            date_str = "-".join(filename.split("-")[2:5])
            file_date = datetime.datetime.strptime(
                date_str, "%Y-%m-%d"
            ).replace(tzinfo=datetime.UTC)
        except ValueError:
            continue

        if datetime.datetime.now(
            tz=datetime.UTC
        ) - file_date > datetime.timedelta(days=days):
            file_path = os.path.join(backups_path, filename)
            os.remove(file_path)
            deleted_files_count += 1

    message = f"Deleted {deleted_files_count} database backup files older than {days} days."
    return message
