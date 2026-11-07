from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from celery import Task
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

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
            tenant_context,
        )

        if schema_name == get_public_schema_name():
            with schema_context(schema_name):
                return super().__call__(*args, **kwargs)

        # ``schema_context(schema_name)`` only sets ``connection.tenant``
        # to a bare ``FakeTenant(schema_name=...)`` (django-tenants
        # internal — carries no other field). Every ``tenant.credentials.*``
        # helper reads real fields off ``connection.tenant``
        # (``getattr(tenant, "acs_api_key", "")`` etc.), so a task running
        # under a FakeTenant would see every per-tenant credential as
        # unconfigured regardless of what the tenant actually has set.
        # ``tenant_context(tenant)`` sets ``connection.tenant`` to the
        # real row instead.
        from tenant.models import Tenant

        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
        except Tenant.DoesNotExist:
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


def run_for_all_tenants(
    task_name: str,
    *,
    include_suspended: bool = False,
    **kwargs: Any,
) -> list[dict[str, str]]:
    """Fan-out a task to all active tenant schemas.

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
    # ``include_suspended`` is for RETENTION sweeps, and only those.
    # The reasoning above is about work a frozen store should not pay
    # for; it does not transfer to deleting data the platform promised
    # to delete. A suspended tenant whose sweeps never ran would keep
    # abandoned uploads and expired files indefinitely, which is a
    # data-protection failure rather than a saving.
    #
    # ``is_active`` stays required either way: an inactive tenant may
    # be mid-offboarding and its schema may already be gone.
    tenants = Tenant.objects.filter(is_active=True).exclude(
        schema_name="public"
    )
    if not include_suspended:
        tenants = tenants.filter(suspended_at__isnull=True)
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

    ``transaction.on_commit`` is reached through the module, not bound at
    import: the test suite monkeypatches it.
    """
    schema = schema_name or getattr(connection, "schema_name", "public")
    headers = {**(options.pop("headers", None) or {}), "_schema_name": schema}

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


#: Characters kept of the error a deferred dispatch records.
_ERROR_LENGTH = 255


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
        "Publishing %s failed after its change committed (%s: %s); it is "
        "kept and published again by the sweep",
        name,
        type(error).__name__,
        error,
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
            last_error=f"{type(error).__name__}: {error}"[:_ERROR_LENGTH],
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
