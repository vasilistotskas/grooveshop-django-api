from __future__ import absolute_import
from __future__ import unicode_literals

from importlib import import_module

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core import management

logger = get_task_logger(__name__)


@shared_task(bind=True, name="Clear Expired Sessions Task")
def clear_expired_sessions_task():
    try:
        management.call_command("clearsessions", verbosity=0)
        return "All expired sessions deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear All Cache Task")
def clear_all_cache_task():
    try:
        management.call_command("clear_cache", verbosity=0)
        return "All cache deleted."
    except Exception as e:
        return f"error: {e}"


@shared_task(bind=True, name="Clear Sessions For None Users Task")
def clear_sessions_for_none_users_task():
    session_store = import_module(settings.SESSION_ENGINE).SessionStore
    non_authenticated_sessions = []

    # Get all session keys
    keys = session_store().keys()

    # Get all non authenticated sessions
    for key in keys:
        session = session_store(session_key=key)
        if not session.get("_auth_user_id"):
            non_authenticated_sessions.append(key)

    # Delete all non authenticated sessions
    session_store().delete_many(non_authenticated_sessions)

    message = f"Cleared {len(non_authenticated_sessions)} non-authenticated sessions."

    logger.info(message)
    return message


@shared_task(bind=True, name="Clear Carts For None Users Task")
def clear_carts_for_none_users_task():
    from cart.models import Cart

    null_carts = Cart.objects.filter(user=None)
    null_carts.delete()

    message = f"Cleared {len(null_carts)} null carts."

    logger.info(message)
    return message


@shared_task(bind=True, name="Cleanup Log Files Task")
def cleanup_log_files_task(self, days=30):
    from django.conf import settings
    from os import path, remove, listdir
    from datetime import datetime, timedelta

    logs_path = path.join(settings.BASE_DIR, "logs")
    files = listdir(logs_path)
    now = datetime.now()

    for file in files:
        file_path = path.join(logs_path, file)
        file_modification_date = datetime.fromtimestamp(path.getmtime(file_path))
        if now - file_modification_date > timedelta(days=days):
            remove(file_path)

    message = f"Removed log files older than {days} days."

    logger.info(message)
    return message
