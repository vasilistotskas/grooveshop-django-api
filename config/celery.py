from datetime import timedelta
from os import getenv

from celery.schedules import crontab

CELERY_BROKER_URL = getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = getenv("CELERY_RESULT_BACKEND", "django-db")
CELERY_CACHE_BACKEND = getenv("CELERY_CACHE_BACKEND", "django-cache")
CELERY_TASK_TRACK_STARTED = True
CELERY_ENABLE_UTC = False
CELERY_BROKER_HEARTBEAT = 0
CELERY_BROKER_POOL_LIMIT = None
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
CELERY_BROKER_CONNECTION_TIMEOUT = 30
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 100
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
CELERY_TIMEZONE = getenv("TIME_ZONE", "Europe/Athens")
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_RESULT_EXTENDED = True
CELERY_TASK_RESULT_EXPIRES = 3600
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Internal settings
BEAT_UPDATE_SEARCH_SEC = int(getenv("BEAT_UPDATE_SEARCH_SEC", 60 * 60 * 8))
BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC = int(
    getenv("BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC", 20)
)

CELERY_BEAT_SCHEDULE = {
    "update-product-translation-search-vectors": {
        "task": "core.tasks.update_product_translation_search_vectors",
        "schedule": timedelta(seconds=BEAT_UPDATE_SEARCH_SEC),
        "options": {"expires": BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC},
    },
    "update-product-translation-search-documents": {
        "task": "core.tasks.update_product_translation_search_documents",
        "schedule": timedelta(seconds=BEAT_UPDATE_SEARCH_SEC),
        "options": {"expires": BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC},
    },
    "update-blog-post-translation-search-vectors": {
        "task": "core.tasks.update_blog_post_translation_search_vectors",
        "schedule": timedelta(seconds=BEAT_UPDATE_SEARCH_SEC),
        "options": {"expires": BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC},
    },
    "update-blog-post-translation-search-documents": {
        "task": "core.tasks.update_blog_post_translation_search_documents",
        "schedule": timedelta(seconds=BEAT_UPDATE_SEARCH_SEC),
        "options": {"expires": BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC},
    },
    "clear-blacklisted-tokens": {
        "task": "core.tasks.tasks.clear_blacklisted_tokens_task",
        "schedule": crontab(hour="2", minute="0"),
    },
    "cleanup-log-files": {
        "task": "core.tasks.cleanup_log_files_task",
        "schedule": crontab(hour="3", minute="0"),
    },
    "clear-carts-for-none-users": {
        "task": "core.tasks.clear_carts_for_none_users_task",
        "schedule": crontab(hour="4", minute="0", day_of_month="*/2"),
    },
    "clear-expired-sessions": {
        "task": "core.tasks.clear_expired_sessions_task",
        "schedule": crontab(hour="5", minute="0", day_of_week="sunday"),
    },
    "clear-all-cache": {
        "task": "core.tasks.clear_all_cache_task",
        "schedule": timedelta(days=30),
    },
    "send-inactive-user-notifications": {
        "task": "core.tasks.send_inactive_user_notifications",
        "schedule": crontab(hour="6", minute="0", day_of_month="1"),
    },
    "monitor-system-health": {
        "task": "core.tasks.monitor_system_health",
        "schedule": crontab(minute="*/30"),
    },
    "backup-database": {
        "task": "core.tasks.backup_database",
        "schedule": crontab(hour="7", minute="0"),
    },
    "optimize-images": {
        "task": "core.tasks.optimize_images",
        "schedule": crontab(hour="3", minute="30", day_of_week="sunday"),
    },
    "cleanup-old-database-backups": {
        "task": "core.tasks.cleanup_old_database_backups",
        "schedule": crontab(hour="12", minute="0"),
    },
}
