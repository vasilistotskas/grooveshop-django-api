"""Celery: broker, results, workers and deferred dispatch."""

import datetime
from os import getenv

from django.core.exceptions import ImproperlyConfigured

from settings.env import PRODUCTION_PROFILE

# Broker & Results
CELERY_BROKER_URL = getenv("CELERY_BROKER_URL", "")
if not CELERY_BROKER_URL:
    if PRODUCTION_PROFILE:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "CELERY_BROKER_URL must be set in production."
        )
    CELERY_BROKER_URL = "amqp://guest:guest@rabbitmq:5672//"
# Not an environment setting: the platform dashboard and the Task Results
# admin read ``django_celery_results``, so the backend must be the
# database one. ``core.result_backend`` is ``django-db`` plus the store a
# failed task belongs to.
CELERY_RESULT_BACKEND = "core.result_backend.TenantDatabaseBackend"

# Task execution
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_SOFT_TIME_LIMIT = 1500
CELERY_TASK_TIME_LIMIT = 1800

# Store FAILURES ONLY.
#
# Nothing in this codebase reads a task's return value: there is no
# AsyncResult, no .get(), no chord and no group. The one chain()
# (core/tasks.py) links an immutable .si() signature, which celery
# dispatches from ``task_request.chain`` before it ever touches the
# backend (celery/app/trace.py). So every stored success was write
# amplification nobody consumed — 2,937 keys a day in production,
# sharing one 614 MiB ``allkeys-lru`` budget with the Django cache,
# where a result could be evicted before it expired and the whole lot
# vanished in the 2026-09-09 Redis outage.
#
# What IS worth keeping is the failures, durably and queryably: a
# silent failure here is an order confirmation nobody receives or a
# voucher that never mints. ``task_store_errors_even_if_ignored`` is
# what makes the pair work — celery/app/trace.py::handle_error_state falls back to it
# precisely when a task is ignored — so successes write nothing while
# failures and retries write a full row, with task name and arguments
# (``result_extended``) for the admin.
#
# Consequences worth knowing before changing this:
#   - A future chord() or group() DOES need results. Set
#     ``ignore_result=False`` on that task rather than flipping this.
#   - "Did the nightly task run at all?" is answered by
#     ``PeriodicTask.last_run_at`` and by MonitoredTask's own success
#     log line, not from this table.
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED = True

# Retention for those failure rows. The name matters: the pre-4.0
# ``CELERY_TASK_RESULT_EXPIRES`` that used to sit here is not a setting
# any more, so under ``namespace="CELERY"`` it was silently ignored and
# the app ran on celery's 1-day default. 30 days is affordable now that
# only failures land here, and it is long enough to still find the row
# behind a complaint that arrives a fortnight late. Beat's
# Celery's scheduler installs ``celery.backend_cleanup`` on its own
# whenever this is set (celery/beat.py).
CELERY_RESULT_EXPIRES = datetime.timedelta(days=30)

# Timezone (use UTC)
CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = getenv("TIME_ZONE", "Europe/Athens")

# Connection settings
CELERY_BROKER_HEARTBEAT = 30
CELERY_BROKER_POOL_LIMIT = 50
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "confirm_publish": True,
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}
CELERY_BROKER_CONNECTION_TIMEOUT = 30
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 100

# Worker settings
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = (
    50  # Restart worker after 50 tasks to prevent memory leaks
)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 250000  # 250MB limit per worker
CELERY_WORKER_DISABLE_RATE_LIMITS = False
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Fetch one task at a time
# Allow ``app.control.pool_restart()`` to recycle worker child processes
# without restarting the pod.
CELERY_WORKER_POOL_RESTARTS = True
# Celery's default is 4.0s: a freshly forked pool child must send its
# "UP" message within that, or the parent kills it and logs
# "Timed out waiting for UP message from <ForkProcess(...)>". Four
# seconds is not a realistic budget for a child that has to import this
# Django app (196 models) inside a CPU-limited container — production
# hit it twice in six hours on 2026-09-15, both times on the hour when
# scheduled tasks bunch up, with the worker measured at 285s spent
# waiting on CPU quota against 128s of CPU actually used.
#
# The CPU ceiling is being raised alongside this (infrastructure), which
# is the real fix; this is the belt to that braces. A fork that is
# merely SLOW should be waited for, not killed and retried — the retry
# forks again into the same contention, which is how the prefork pool
# gets into a fork/SIGKILL loop that consumes no tasks at all.
CELERY_WORKER_PROC_ALIVE_TIMEOUT = 30.0

CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 3
CELERY_RESULT_EXTENDED = True

# Serialization
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"

# Search indexing has its own queue, which every worker consumes beside
# ``celery`` (``-Q celery,meilisearch``). Celery 5.7 deprecates a task's
# own ``queue=`` (removed in 6.0) in favour of this setting.
CELERY_TASK_ROUTES = {"meili.*": {"queue": "meilisearch"}}

# Beat scheduler
CELERY_BEAT_SCHEDULER = "scheduler.beat:CodeOwnedScheduler"
CELERY_BEAT_MAX_LOOP_INTERVAL = 300

# Development safety
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

# Deferred dispatches (``tenant.models.DeferredDispatch``): the sweep takes
# this many per run, waits ``base * 2**attempts`` seconds (at most the cap)
# between tries, and deletes a row, with an ERROR, once it is this old.
DEFERRED_DISPATCH_BATCH = int(getenv("DEFERRED_DISPATCH_BATCH", "200"))
DEFERRED_DISPATCH_BACKOFF_SECONDS = int(
    getenv("DEFERRED_DISPATCH_BACKOFF_SECONDS", "30")
)
DEFERRED_DISPATCH_BACKOFF_CAP_SECONDS = int(
    getenv("DEFERRED_DISPATCH_BACKOFF_CAP_SECONDS", "3600")
)
DEFERRED_DISPATCH_BROKER_DOWN_SECONDS = int(
    getenv("DEFERRED_DISPATCH_BROKER_DOWN_SECONDS", "30")
)
DEFERRED_DISPATCH_RETENTION_DAYS = int(
    getenv("DEFERRED_DISPATCH_RETENTION_DAYS", "7")
)
