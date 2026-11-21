"""Celery: broker, results, workers and deferred dispatch."""

import datetime
import math
import sys
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
# (src/core/tasks.py) links an immutable .si() signature, which celery
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
# AMQP heartbeats, in seconds, of a worker's consuming connection and its
# event connection (the only ones opened with it). RabbitMQ closes the
# consuming one after two missed intervals and redelivers every acks_late
# task the worker is still running. On the event connection a miss is never
# fatal: nothing reads it between publishes
# (``core.broker.PublishingConnection``, #721).
CELERY_BROKER_HEARTBEAT = 30
CELERY_BROKER_POOL_LIMIT = 50
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "confirm_publish": True,
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}
# How long one read on a PUBLISHING broker connection may block, in
# seconds: py-amqp's ``read_timeout`` (``SO_RCVTIMEO``) on the connections
# ``core.broker.PublishingTransport`` opens (tasks, control commands, beat,
# worker events), never on a worker's consuming connection. It bounds a
# reply the broker never sends, such as the publish confirm of a
# connection it blocked without sending ``connection.blocked``, or the
# ``Close-Ok`` of one kombu closes after a failed publish. Without a bound
# a request that published to a blocked broker waited without end (#509).
#
# Every synchronous reply on those connections must arrive within it: a
# confirm, a declare, a channel open. A healthy broker answers in
# milliseconds (49 ms at worst measured with half a CPU, as in production;
# docs/deferred-dispatch.md), so 5 s is ample headroom for a slow one.
#
# Linux only, where every deployment runs: py-amqp packs the value as a
# POSIX ``timeval``, which Windows reads as a DWORD of MILLISECONDS, so the
# same 5 would cut every read on a Windows host at 5 ms.
BROKER_PUBLISH_READ_TIMEOUT = (
    None
    if sys.platform == "win32"
    else float(getenv("BROKER_PUBLISH_READ_TIMEOUT", "5"))
)
# How long a process publishes nothing after RabbitMQ blocked one of its
# publishing connections (``connection.blocked``: a memory or disk alarm),
# in seconds. Every publish is refused before it writes until the broker
# says ``connection.unblocked`` or this passes; then the next publish
# first asks again with a message no queue receives (``core.broker``).
#
# RabbitMQ sends ``connection.unblocked`` only on the connection it
# blocked, which kombu has dropped by then, so in practice this is how
# soon publishing resumes after an alarm clears: rows kept by
# ``dispatch_on_commit`` are swept, and beat sends a missed entry again
# after ``PUBLISH_RETRY_SECONDS``. Each question asked during an alarm
# leaves one connection the broker no longer reads until the alarm clears,
# so a shorter hold leaves more of them on a broker already short of
# memory: one per minute per process that publishes.
BROKER_BLOCKED_HOLD_SECONDS = float(getenv("BROKER_BLOCKED_HOLD_SECONDS", "60"))
if not (
    math.isfinite(BROKER_BLOCKED_HOLD_SECONDS)
    and BROKER_BLOCKED_HOLD_SECONDS > 0
):
    # Without a hold, worker events and heartbeats (which kombu retries
    # without end) reconnect to a blocked broker in a loop; an endless one
    # refuses every publish for good.
    raise ImproperlyConfigured(
        "BROKER_BLOCKED_HOLD_SECONDS must be a finite number of seconds"
        " above 0."
    )
# How often kombu publishes a task again after a connection error, each
# time on a new connection (``kombu.Connection.ensure``). One retry is what
# a dead pooled connection needs: after a broker restart the first publish
# finds the pooled connection reset and the retry reconnects (measured:
# with 0 that publish fails, with 1 it goes out). The same retry drops a
# connection RabbitMQ blocked, and ends there: the new connection refuses
# to publish (``core.broker``). Every further retry only adds a copy when a
# publish times out, since the broker still delivers what it was sent, and
# ``dispatch_on_commit`` already keeps the failure as a ``DeferredDispatch``
# that the sweep publishes again. The intervals are Celery's defaults.
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": int(getenv("CELERY_TASK_PUBLISH_MAX_RETRIES", "1")),
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 1,
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
