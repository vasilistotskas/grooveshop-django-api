import logging
import os
from typing import Any

from celery import Celery
from celery.app.control import Control
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_postrun,
    task_prerun,
    worker_process_init,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

logger = logging.getLogger(__name__)

# Mirrors the version tick tracked by core.middleware.translation_reload
# on web pods. Workers don't serve HTTP, so they need their own check
# to pick up Rosetta edits without a pod restart.
_worker_translation_version: float | None = None


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from django.conf import settings  # noqa: F401


def _is_query(command: str) -> bool:
    """Whether a remote control command only reads a worker's state.

    Celery registers each command as ``inspect`` (``ping``, ``hello``,
    ``stats``, ``active``, ...) or ``control`` (``revoke``, ``shutdown``,
    ``pool_restart``, ``rate_limit``, ...) in ``celery.worker.control``;
    a name it does not know counts as a command.
    """
    from celery.worker.control import Panel

    info = Panel.meta.get(command)
    return info is not None and info.type == "inspect"


class GrooveShopControl(Control):
    """Remote control whose queries get no answer while the broker blocks.

    A query (an ``inspect`` command: ``ping``, ``stats``, a worker's
    ``hello`` to its neighbours) cannot be asked while this process holds
    off a blocked broker (``core.broker``), so no worker answers it: it
    returns no replies, as when no worker is up, with a WARNING. A worker
    that starts, or restarts its consumer, during an alarm therefore goes
    on alone instead of exiting with "Unrecoverable error: BrokerBlocked"
    (its ``Mingle`` step treats any other error as fatal, #721). It skips
    the sync of revoked task ids and clocks with its neighbours, and
    ``Mingle`` does not run again once the alarm clears.

    Every other command (``revoke``, ``shutdown``, ``pool_restart``,
    ``rate_limit``, ...) still raises the refusal, whether or not it waits
    for replies: the ``celery control`` command line always does, and an
    operator must see that it was not delivered.
    """

    def broadcast(self, command: str, *args: Any, **kwargs: Any) -> Any:
        from core.broker import BrokerBlocked, unanswered_query

        try:
            return super().broadcast(command, *args, **kwargs)
        except BrokerBlocked:
            if not _is_query(command):
                raise
            unanswered_query.report(command)
            return []


class GrooveShopCelery(Celery):
    """Celery whose publishing connections stop while RabbitMQ blocks them.

    Celery opens every connection it publishes on through
    ``connection_for_write``: the producer pool behind tasks and control
    commands, beat's producer, a worker's event dispatcher. Those use
    ``core.broker.PublishingTransport``. A worker's consuming connection
    (``connection_for_read``) keeps kombu's own transport: a read that times
    out there makes the worker reconnect, and RabbitMQ then redelivers every
    task the worker was still running (``task_acks_late``).

    Its remote control is ``GrooveShopControl``.
    """

    control_cls = "core.celery:GrooveShopControl"

    def connection_for_write(self, url: Any = None, **kwargs: Any) -> Any:
        from core.broker import PublishingTransport

        # ``Celery.connection()`` passes ``transport`` itself, as ``None``
        # unless its caller chose one.
        kwargs["transport"] = kwargs.get("transport") or PublishingTransport
        return super().connection_for_write(url, **kwargs)

    def send_task(self, *args: Any, **kwargs: Any) -> Any:
        """Publish a task, deciding what a broker block does to it.

        From a task the worker is running (``self.retry()``, ``.delay()``)
        the publish is never refused: ``Task.retry`` turns a failed publish
        into ``Reject(requeue=False)``, which drops an ``acks_late``
        message, so the task waits through the block instead
        (``core.broker.waiting_through_blocks``).

        Anywhere else it is refused while the broker blocks this process,
        before a pooled connection is taken: the transport would refuse it
        too, but kombu replaces a pooled connection after any failed
        publish, so each refusal there would cost a new connection to a
        broker short of memory.
        """
        from core.broker import refuse_while_held, waiting_through_blocks

        if self.current_worker_task is not None:
            with waiting_through_blocks():
                return super().send_task(*args, **kwargs)
        refuse_while_held()
        return super().send_task(*args, **kwargs)


def create_celery_app():
    tasker = GrooveShopCelery("core")

    # Every Celery option lives in settings/celery.py under the CELERY_
    # namespace.
    #
    # A ``conf.update()`` here does NOT win, whatever the call order: a key
    # supplied by ``config_from_object`` is resolved from that source each
    # time it is read, so a later literal is shadowed rather than applied.
    # This block used to carry a comment claiming the opposite, and with it
    # ``task_ignore_result=False``, ``task_soft_time_limit=300`` and
    # ``task_time_limit=600`` — none of which were ever in effect (the real
    # values are True, 1500 and 1800). Add options to settings/celery.py and
    # resolve ``celery_app.conf[...]`` to check one, never infer from here.
    tasker.config_from_object("django.conf:settings", namespace="CELERY")

    tasker.autodiscover_tasks()

    # Minimal connection management - let Django handle most of it
    from celery.signals import worker_process_shutdown

    # ``weak=False`` on every handler below. They are closures defined
    # inside this function, so under Celery's default weak references the
    # only strong reference dies when ``create_celery_app`` returns and
    # every receiver is garbage-collected. Verified against the real
    # module: all six reported ``has_listeners=False``.
    #
    # A signal that never fires raises nothing, which is why this held
    # for so long — and why the comment below claiming the overlay fixed
    # "Order Received - #38 shipped in English" described something that
    # was not happening. ``meili/apps.py`` documents the identical trap
    # for its own closures.
    @worker_process_init.connect(weak=False)
    def init_worker_process(**kwargs):
        """Initialize worker process with clean connections + translations."""
        from django.db import close_old_connections

        # Just close any stale connections from parent process
        close_old_connections()

        # Seed in-memory gettext catalogs with DB-backed msgstrs. Without
        # this, a worker only sees the .po/.mo values baked into the image
        # and stays blind to every Rosetta save that lands post-deploy —
        # which is exactly what caused "Order Received - #38" to ship in
        # English even though the Greek msgstr was in the Translation
        # table on Postgres.
        try:
            from core.rosetta_storage import apply_db_overlay

            apply_db_overlay()
        except Exception as exc:
            logger.warning(
                "Could not apply translation overlay at worker init: %s", exc
            )

        logger.info("Worker process initialized")

    @task_prerun.connect(weak=False)
    def refresh_translations_if_bumped(**kwargs):
        """Re-apply the DB overlay when another pod bumped the version tick.

        Mirrors core.middleware.translation_reload for the worker side.
        Each task pays one cache.get() — a few bytes over Redis, already
        hot per worker process — to see if a Rosetta save has happened
        since the last check. No rebuild if unchanged.
        """
        global _worker_translation_version

        from django.core.cache import cache

        from core.rosetta_storage import (
            TRANSLATION_VERSION_CACHE_KEY,
            _reload_translations,
            apply_db_overlay,
        )

        try:
            remote_version = cache.get(TRANSLATION_VERSION_CACHE_KEY)
        except Exception:
            return

        if remote_version is None:
            return

        if _worker_translation_version == remote_version:
            return

        try:
            # Order matters: reload (evict + rebuild the base gettext
            # catalogs) FIRST, then apply the DB overlay on top. The reverse
            # order discarded the overlay because _reload_translations()
            # rebuilds the catalogs from scratch (G0105).
            _reload_translations()
            apply_db_overlay()
        except Exception:
            logger.exception(
                "Failed to refresh worker translations after tick %s",
                remote_version,
            )
            return

        _worker_translation_version = remote_version
        logger.info(
            "Worker refreshed translations from DB (version %s)",
            remote_version,
        )

    @worker_process_shutdown.connect(weak=False)
    def shutdown_worker_process(**kwargs):
        """Clean up when worker shuts down."""
        from django.db import close_old_connections

        close_old_connections()
        logger.info("Worker process shutting down")

    # ------------------------------------------------------------------ #
    # Correlation-ID propagation: web → Celery task                       #
    # ------------------------------------------------------------------ #
    # Inject the active correlation-id into every task's headers at the
    # moment the task is published.  The worker side reads it back in the
    # task_prerun handler below and binds it to the ContextVar so all log
    # records emitted during the task carry the same correlation_id that
    # the originating HTTP request had.
    @before_task_publish.connect(
        dispatch_uid="core.celery.inject_correlation_id", weak=False
    )
    def inject_correlation_id(headers: dict, **kwargs) -> None:
        """Stamp the outgoing task message with the current correlation id."""
        from core.middleware.correlation_id import get_correlation_id

        cid = get_correlation_id()
        if cid and cid != "-":
            headers["correlation_id"] = cid

    # Maps task_id → ContextVar token so we can reset after the task.
    _cid_tokens: dict = {}

    @task_prerun.connect(
        dispatch_uid="core.celery.restore_correlation_id", weak=False
    )
    def restore_correlation_id(task_id: str, task, **kwargs) -> None:
        """Restore correlation id from task headers into the worker context.

        Celery merges ``task.request.headers`` from the message at this
        point, so ``task.request.get("correlation_id")`` is already
        populated.  Binding it to the ContextVar makes the value
        available to ``CorrelationIdFilter`` so every log line the task
        emits carries the originating request's correlation id.

        The reset token is stashed in ``_cid_tokens`` so the matching
        ``cleanup_correlation_id`` handler can restore the previous value
        and prevent the correlation id from leaking into subsequent tasks
        on the same worker thread.
        """
        from core.middleware.correlation_id import set_correlation_id

        cid = (task.request.headers or {}).get("correlation_id")
        if cid:
            token = set_correlation_id(cid)
            _cid_tokens[task_id] = token

    @task_postrun.connect(
        dispatch_uid="core.celery.cleanup_correlation_id", weak=False
    )
    def cleanup_correlation_id(task_id: str, **kwargs) -> None:
        """Reset the correlation id ContextVar after the task completes.

        Prevents the correlation id set in ``restore_correlation_id`` from
        leaking into the next task executed on the same worker thread.
        """
        from core.middleware.correlation_id import reset_correlation_id

        token = _cid_tokens.pop(task_id, None)
        if token is not None:
            reset_correlation_id(token)

    return tasker


celery_app = create_celery_app()
