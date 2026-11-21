from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import psycopg
from celery import Task, current_task
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import OperationalError, connection, transaction
from psycopg.errors import LockNotAvailable

from core.retry import TRANSIENT_NETWORK_ERRORS

logger = logging.getLogger(__name__)


class TenantTask(Task):
    """Base Celery task that propagates tenant schema context.

    Schema resolution at enqueue time prefers an explicit
    ``_schema_name`` header from the caller (so cross-schema
    on_commit and fanout paths can pin the value) and falls back to
    the thread-local ``connection.schema_name``. Without the
    explicit hand-off, dispatchers
    fired from worker callbacks or management commands stamp
    ``'public'`` and the worker runs against the wrong schema.

    ``__call__`` reads the header back from ``request.headers``. Celery
    puts a custom header in two places: a worker flattens the message
    headers onto the request, and ``Context`` then also collects every
    non-Celery key into ``request.headers``; an eagerly applied task
    (``CELERY_TASK_ALWAYS_EAGER``, the test suite) is handed ``headers``
    as one key and ``Context`` keeps it as given, so the schema is never
    a request attribute there. ``request.headers`` is the one place both
    paths agree on.
    """

    def apply_async(self, *args: Any, **options: Any) -> Any:
        headers = options.pop("headers", {}) or {}
        if not headers.get("_schema_name"):
            headers["_schema_name"] = getattr(
                connection, "schema_name", "public"
            )
        options["headers"] = headers
        return super().apply_async(*args, **options)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        headers = self.request.headers if self.request else None
        schema_name = (headers or {}).get("_schema_name") or "public"

        from django_tenants.utils import (
            get_public_schema_name,
            schema_context,
        )

        if schema_name == get_public_schema_name():
            with schema_context(schema_name):
                return super().__call__(*args, **kwargs)

        with holding_store(schema_name):
            return self._run_in_store(schema_name, *args, **kwargs)

    def _run_in_store(self, schema_name: str, *args: Any, **kwargs: Any) -> Any:
        from django_tenants.utils import schema_context, tenant_context

        from tenant.models import Tenant, TenantArchive

        # ``schema_context(schema_name)`` only sets ``connection.tenant``
        # to a bare ``FakeTenant(schema_name=...)`` (django-tenants
        # internal — carries no other field). Every ``tenant.credentials.*``
        # helper reads real fields off ``connection.tenant``
        # (``getattr(tenant, "acs_api_key", "")`` etc.), so a task running
        # under a FakeTenant would see every per-tenant credential as
        # unconfigured regardless of what the tenant actually has set.
        # ``tenant_context(tenant)`` sets ``connection.tenant`` to the
        # real row instead.
        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
        except Tenant.DoesNotExist:
            # The store was destroyed after the task was queued: a fan-out
            # reaches suspended stores, which are the ones that can be
            # destroyed, and ``destroy_tenant`` records each one it drops in
            # ``TenantArchive``. With the schema gone the search path falls
            # through to public, so the task would read and write the
            # platform's own tables of every app the two share.
            if TenantArchive.objects.filter(schema_name=schema_name).exists():
                logger.warning(
                    "TenantTask: the store of schema=%s was destroyed; %s "
                    "not run",
                    schema_name,
                    self.name,
                )
                return None
            logger.warning(
                "TenantTask: no Tenant row for schema=%s — falling back to "
                "a bare schema context (tenant.credentials.* will read as "
                "unconfigured)",
                schema_name,
            )
            with schema_context(schema_name):
                return super().__call__(*args, **kwargs)

        with tenant_context(tenant):
            return super().__call__(*args, **kwargs)


def _store_lock_key(schema_name: str) -> str:
    return f"store-tasks:{schema_name}"


@contextmanager
def holding_store(schema_name: str) -> Generator[None]:
    """Keep the store of *schema_name* from being destroyed while a task
    runs in it.

    ``destroy_tenant`` drops the schema between two of a task's
    transactions. The task's next query then falls through the search
    path to public's table of any app both schemas have (``user``,
    ``emails``, ``admin``): a sweep's next batch deleted the platform's own
    rows, by the store's retention window. A task holds a shared advisory
    lock on its store while it runs, and ``destroy_tenant`` takes it
    exclusively (``hold_store_for_destroy``), so a destroy waits for the
    store's running tasks and a task waits for a destroy in progress. That
    task then finds no row and is not run (``TenantArchive``).

    Inside a transaction (a task called within one, or applied eagerly)
    the lock is the transaction's and ends with it. Otherwise it is held on
    a database session of its own, opened for the task and closed when the
    task ends. The task's own connection can then close mid-run
    (``meilisearch_sync_all_indexes`` does), go back to the connection
    pool or break, and the store stays held until the task ends: a lock on
    that connection would be lost or left behind with it.

    The session has no ``statement_timeout`` and no ``lock_timeout``, so a
    task that starts during a destroy waits for it and is then skipped,
    instead of failing on the worker connection's 30 s statement timeout.
    The destroy bounds that wait: it waits ``DESTROY_TASKS_WAIT`` for the
    running tasks, then holds the store only for its own transaction, whose
    statements run under the connection's timeouts. The task's hard time
    limit is the last bound.
    """
    key = _store_lock_key(schema_name)
    if connection.in_atomic_block:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))",
                [key],
            )
        yield
        return
    params = connection.get_connection_params()
    params["autocommit"] = True
    params["options"] = (
        f"{params.get('options', '')} -c statement_timeout=0 -c lock_timeout=0"
    ).strip()
    with connection.wrap_database_errors:
        session = psycopg.connect(**params)
    try:
        with connection.wrap_database_errors:
            session.execute(
                "SELECT pg_advisory_lock_shared(hashtextextended(%s, 0))",
                [key],
            )
        yield
    finally:
        # Unlocked first so the store is free when the task returns;
        # closing alone frees it only once the server ends the session.
        try:
            session.execute(
                "SELECT pg_advisory_unlock_shared(hashtextextended(%s, 0))",
                [key],
            )
        except psycopg.Error:
            logger.warning(
                "TenantTask: the store lock session of schema=%s broke "
                "during the task; the lock went with it",
                schema_name,
            )
        finally:
            session.close()


def hold_store_for_destroy(schema_name: str, *, wait: timedelta) -> bool:
    """Take the store's task lock exclusively for the current transaction.

    Waits up to *wait* for the tasks running in the store to end; ``False``
    when they did not. Tasks that start meanwhile wait for the transaction
    to end (``holding_store``). For this one statement ``lock_timeout`` is
    *wait* and ``statement_timeout`` is off, so the wait ends as a lock
    timeout and never as the connection's statement timeout (30 s, the
    same length, outside CI); both are put back afterwards.
    """
    key = _store_lock_key(schema_name)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('lock_timeout'), "
            "current_setting('statement_timeout')"
        )
        previous_lock, previous_statement = cursor.fetchone()
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true), "
            "set_config('statement_timeout', '0', true)",
            [f"{int(wait.total_seconds() * 1000)}ms"],
        )
        try:
            with transaction.atomic():
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    [key],
                )
        except OperationalError as exc:
            if not isinstance(exc.__cause__, LockNotAvailable):
                raise
            return False
        finally:
            cursor.execute(
                "SELECT set_config('lock_timeout', %s, true), "
                "set_config('statement_timeout', %s, true)",
                [previous_lock, previous_statement],
            )
    return True


def run_for_all_tenants(
    task_name: str,
    *,
    include_suspended: bool = False,
    **kwargs: Any,
) -> list[dict[str, str]]:
    """Fan-out a task to every active store, or every store.

    ``include_suspended`` reaches suspended stores too
    (``Tenant.objects.stores()`` against ``Tenant.objects.active()``).

    Returns JSON-serializable dispatch records, NOT ``AsyncResult``
    objects. Celery encodes a task's return value into the result
    backend using the configured serializer (JSON here), and an
    ``AsyncResult`` is not JSON-serializable — so returning them raised
    ``EncodeError`` *after* the fan-out had already dispatched every
    subtask. The scheduled work ran fine, but each fanout task was
    recorded as FAILED, so every beat tick produced a failure and real
    failures were indistinguishable from the noise. Observed in
    production 2026-08-21 across all six beat-driven fanouts.
    """
    from tenant.models import Tenant

    results: list[dict[str, str]] = []
    # Skip suspended tenants — a suspended operator's beat-driven work
    # (poll carriers, reconcile payouts, sync lockers/stations) must not
    # fire: it would burn the carrier API budget and mutate a frozen
    # tenant's data. Mirrors the webhook resolvers' suspended_at filter.
    #
    # ``include_suspended`` is for two kinds of work, and only those.
    # The reasoning above is about work a frozen store should not pay
    # for, and neither kind is that:
    #
    # - RETENTION sweeps. Deleting data the platform promised to delete
    #   does not stop with the store. A suspended tenant whose sweeps
    #   never ran would keep abandoned uploads and expired files
    #   indefinitely, which is a data-protection failure rather than a
    #   saving.
    # - Finishing something a CUSTOMER's money or parcel waits on, which
    #   was started before the store was suspended: a refund still
    #   pending at the provider (``reconcile_pending_refunds``), the
    #   books of money already moved (``allocate_credit_sources``), a
    #   BoxNow creation of uncertain outcome
    #   (``settle_uncertain_boxnow_creations``), the courier's cancel for
    #   an order already canceled (``finish_pending_shipment_cancels``).
    #   Left undone, the customer's refund hangs or a canceled order's
    #   parcel is delivered.
    #
    # Anything that starts new work for the store (polls, syncs, mail)
    # stays off. What finishing such work sets off divides the same way:
    # what the store owes its customer still runs (the refund confirmation
    # email, the myDATA credit document or receipt), marketing and
    # analytics do not (``meta_capi.services.is_capi_enabled`` refuses a
    # suspended store). The agent outbox sweep is passed the flag too and
    # sends nothing for a suspended store (``agent.outbox._store_is_active``
    # holds its events).
    #
    # A store being destroyed needs no filter: ``destroy_tenant`` drops
    # its schema and deletes its row in one transaction, so every row read
    # here has its schema. It first waits for the tasks running in the
    # store (``holding_store``). A task queued for it that starts after the
    # destroy commits finds no row, and ``TenantTask`` does not run it
    # (``TenantArchive``).
    tenants = (
        Tenant.objects.stores()
        if include_suspended
        else Tenant.objects.active()
    )
    for tenant in tenants:
        from core import celery_app

        # ``ignore_result`` has to be passed explicitly here.
        # ``Task.apply_async`` does ``options.setdefault('ignore_result',
        # self.ignore_result)``, but ``Celery.send_task`` — which this
        # uses, because a fan-out is given a task NAME rather than a
        # task object — defaults it to ``False`` and never consults the
        # task. The message then carries an explicit False, and the
        # worker honours an explicit header over the task's own setting,
        # so every fanned-out task stored a SUCCESS row that nothing
        # reads. Seen in production the hour CELERY_TASK_IGNORE_RESULT
        # landed: 12 rows in ten minutes, all of them fan-out subtasks.
        task = celery_app.tasks.get(task_name)
        ignore_result = (
            task.ignore_result
            if task is not None
            else celery_app.conf.task_ignore_result
        )

        result = celery_app.send_task(
            task_name,
            kwargs=kwargs,
            headers={"_schema_name": tenant.schema_name},
            ignore_result=ignore_result,
        )
        results.append(
            {"schema_name": tenant.schema_name, "task_id": str(result.id)}
        )
    return results


class BrokerKnownDown(Exception):
    """A publish that was not tried, because one failed moments ago."""

    def __str__(self) -> str:
        return "not tried: a publish in this process failed moments ago"


#: When this process may try the broker again (``time.monotonic``). One failed
#: publish holds its caller for the whole of Celery's connection retries, and
#: an order makes six: an outage must not make a shopper wait for each.
_broker_down_until = 0.0


def _broker_known_down() -> bool:
    return time.monotonic() < _broker_down_until


def _note_broker_down() -> None:
    global _broker_down_until
    _broker_down_until = (
        time.monotonic() + settings.DEFERRED_DISPATCH_BROKER_DOWN_SECONDS
    )


def dispatch_on_commit(
    # Annotated `Any`, not `Task`: Celery's `@shared_task` returns a Task
    # at RUNTIME, but a type checker sees the undecorated function, so a
    # `Task` annotation is an error at every call site rather than a
    # guarantee. What this needs of the argument is `.apply_async`.
    task: Any,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    *,
    schema_name: str | None = None,
    then: Callable[[], None] | None = None,
    **options: Any,
) -> None:
    """Queue ``task`` for after the current transaction, schema pinned NOW.

    ``TenantTask.apply_async`` falls back to ``connection.schema_name``,
    but a commit hook runs after the request's schema context can
    unwind — a Stripe replay, a management command, a webhook loop over
    tenants — and by then the connection has usually snapped back to
    ``public``. The task then runs against the wrong schema and fails on
    ``DoesNotExist``, or worse, does not.

    The fix cannot live inside ``TenantTask``: by the time the hook runs
    the caller's frame is gone, and wrapping ``on_commit`` globally would
    break ``captureOnCommitCallbacks`` and every hook that is not a task.
    An explicit hand-off is the one supported idiom, and this is it —
    ``connection.schema_name`` is read HERE, at registration, and carried
    in the header ``TenantTask.apply_async`` already honours.

    ``args`` and ``kwargs`` are omitted from the call when not given,
    rather than passed as empty, so the resulting ``apply_async``
    signature is the one the call sites already produce.

    A publish that fails because the broker or the network is unreachable
    never reaches the caller: the change it follows is already committed, so
    raising could only answer 500 for work that was done. The dispatch is
    logged at ERROR and kept as a ``DeferredDispatch`` that
    ``redispatch_deferred_tasks`` publishes again, so it is not lost. Celery
    raises ``OperationalError`` for every connection error of the transport,
    which for RabbitMQ includes any ``OSError``, a TLS failure and a nacked
    message, so those are kept too. Anything else, such as a ``TypeError``
    from bad arguments, is a bug and still raises. After one such failure this
    process keeps the next ones without trying, for
    ``DEFERRED_DISPATCH_BROKER_DOWN_SECONDS``, so one request does not wait
    out the failure once per task.

    ``then`` runs once the task is published or kept, in the pinned schema.
    It does not run when the row could not be written either, so what it
    records never outlives a message that was neither sent nor kept. What it
    raises reaches the caller and drops the commit's later hooks, like any
    hook that raises, so it should do no more than record.

    One call is one dispatch with one task id, chosen here and passed as
    ``task_id``. A publish that wrote its message before it failed is
    delivered as well as the row the sweep publishes again, and a worker
    lost under ``acks_late`` has the message delivered again; every retry
    keeps the id too. A task that keys its effect on its own id
    (``emails.log.delivery_id``) therefore does it once however many of
    those copies run (docs/deferred-dispatch.md).

    ``transaction.on_commit`` is reached through the module, not bound at
    import: the test suite monkeypatches it.
    """
    schema = schema_name or getattr(connection, "schema_name", "public")
    headers = {**(options.pop("headers", None) or {}), "_schema_name": schema}
    options.setdefault("task_id", str(uuid.uuid4()))

    call: dict[str, Any] = {}
    if args is not None:
        call["args"] = list(args)
    if kwargs is not None:
        call["kwargs"] = dict(kwargs)

    def handed_over() -> bool:
        if _broker_known_down():
            return defer_dispatch(
                task, call, headers, options, BrokerKnownDown()
            )
        try:
            task.apply_async(**call, headers=headers, **options)
        except TRANSIENT_NETWORK_ERRORS as exc:
            _note_broker_down()
            return defer_dispatch(task, call, headers, options, exc)
        return True

    def publish() -> None:
        if handed_over() and then is not None:
            from django_tenants.utils import schema_context

            with schema_context(schema):
                then()

    transaction.on_commit(publish)


def dispatch_key(namespace: str, *parts: object) -> uuid.UUID | None:
    """The key of one effect of the dispatch the running task serves, or
    None outside a task.

    Every copy of a dispatch runs under its task id (``dispatch_on_commit``),
    so a key made of that id and of what tells the task's effects apart
    (*parts*: a recipient, a subject) is the same in every copy, and a row
    unique on it is written once. *namespace* keeps two kinds of effect
    apart. Outside a task there is no copy to tell apart.
    """
    task_id = getattr(getattr(current_task, "request", None), "id", None)
    if not task_id:
        return None
    return uuid.uuid5(
        uuid.uuid5(uuid.NAMESPACE_URL, f"grooveshop:{namespace}"),
        "|".join(str(part) for part in (task_id, *parts)),
    )


#: Characters kept of the error a deferred dispatch records.
_ERROR_LENGTH = 255


def describe_error(error: BaseException) -> str:
    """``Type: reason`` for a failed publish, with a reason that says something.

    kombu re-raises a connection error as ``OperationalError(str(exc))`` from
    it, and the socket timeout py-amqp raises carries no text, so a publish
    to a blocked broker read ``OperationalError: `` and nothing more. An
    error without text is described by its cause (``raise ... from``), and a
    bare timeout as ``timed out``, the text Python's own socket timeout has.
    """
    reason = ""
    link: BaseException | None = error
    last = error
    while link is not None and not reason:
        reason = str(link) or (
            "timed out" if isinstance(link, TimeoutError) else ""
        )
        last = link
        link = link.__cause__
    return f"{type(error).__name__}: {reason or type(last).__name__}"


def defer_dispatch(
    task: Any,
    call: Mapping[str, Any],
    headers: Mapping[str, Any],
    options: Mapping[str, Any],
    error: BaseException,
) -> bool:
    """Keep a dispatch whose publish failed, for ``redispatch_deferred_tasks``.

    Returns whether it was kept. If the row cannot be written either, the
    log line says what the work was, so it can still be recovered by hand,
    with its secrets masked: the row may hold a reset link, a log may not.
    """
    from django.utils import timezone

    from tenant.models import DeferredDispatch

    name = getattr(task, "name", repr(task))
    payload = {
        "args": call.get("args", []),
        "kwargs": call.get("kwargs", {}),
        "headers": dict(headers),
        "options": dict(options),
    }
    logger.error(
        "Publishing %s failed after its change committed (%s); it is "
        "kept and published again by the sweep",
        name,
        describe_error(error),
        extra={"task": name, "schema": headers.get("_schema_name", "")},
    )
    try:
        DeferredDispatch.objects.create(
            task_name=name,
            args=payload["args"],
            kwargs=payload["kwargs"],
            headers=payload["headers"],
            options=json.loads(
                json.dumps(payload["options"], cls=DjangoJSONEncoder)
            ),
            schema_name=str(headers.get("_schema_name", ""))[:63],
            last_error=describe_error(error)[:_ERROR_LENGTH],
            next_attempt_at=timezone.now(),
        )
    except Exception:
        from core.tasks import safe_task_arguments

        safe_args, safe_kwargs = safe_task_arguments(
            task, payload["args"], payload["kwargs"]
        )
        logger.exception(
            "A deferred dispatch of %s could not be kept; payload args=%s "
            "kwargs=%s headers=%r options=%r",
            name,
            safe_args,
            safe_kwargs,
            payload["headers"],
            payload["options"],
        )
        return False
    return True


def publish_or_drop(task: Any, /, **kwargs: Any) -> None:
    """Publish what an outage may lose, now: an analytics beacon.

    For an endpoint open to anonymous traffic, where a ``DeferredDispatch``
    row per request in an outage would turn that traffic into database rows.
    It shares ``dispatch_on_commit``'s window: a broker known to be down is
    not tried, so a visitor's request does not wait for it, and one failure
    here spares the next order the wait too. Only the failure that opens the
    window is logged, so an outage does not log every beacon it drops.
    """
    if _broker_known_down():
        return
    try:
        task.delay(**kwargs)
    except TRANSIENT_NETWORK_ERRORS:
        _note_broker_down()
        logger.exception(
            "Dropped %s: the broker is unreachable, and this process drops "
            "its beacons for the next %s s",
            getattr(task, "name", repr(task)),
            settings.DEFERRED_DISPATCH_BROKER_DOWN_SECONDS,
        )
