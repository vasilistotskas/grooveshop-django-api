from datetime import timedelta
from os import getenv

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
CELERY_TASK_TIME_LIMIT = 5 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_RESULT_EXTENDED = True
CELERY_TASK_RESULT_EXPIRES = 3600
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Internal settings
BEAT_UPDATE_SEARCH_SEC = getenv("BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC", 20)
BEAT_UPDATE_SEARCH_EXPIRE_AFTER_SEC = BEAT_UPDATE_SEARCH_SEC
UPDATE_SEARCH_VECTOR_INDEX_QUEUE_NAME = getenv(
    "UPDATE_SEARCH_VECTOR_INDEX_QUEUE_NAME", "update_search_vector_index"
)
UPDATE_SEARCH_DOCUMENT_INDEX_QUEUE_NAME = getenv(
    "UPDATE_SEARCH_DOCUMENT_INDEX_QUEUE_NAME", "update_search_document_index"
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
    "clear-blacklisted-tokens": {
        "task": "core.tasks.tasks.clear_blacklisted_tokens_task",
        "schedule": timedelta(hours=24),
    },
    "cleanup-log-files": {
        "task": "core.tasks.cleanup_log_files_task",
        "schedule": timedelta(hours=24),
    },
    "clear-carts-for-none-users": {
        "task": "core.tasks.clear_carts_for_none_users_task",
        "schedule": timedelta(hours=24),
    },
    "clear-expired-sessions": {
        "task": "core.tasks.clear_expired_sessions_task",
        "schedule": timedelta(hours=24),
    },
    "clear-all-cache": {
        "task": "core.tasks.clear_all_cache_task",
        "schedule": timedelta(days=30),
    },
}
