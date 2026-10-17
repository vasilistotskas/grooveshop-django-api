"""The Celery result backend: ``django-db`` that also records the store.

Task failures from every store land in ONE public-schema table
(``django_celery_results_taskresult``), so a row has to say whose it is.
Every message is published with the schema it belongs to in its
``_schema_name`` header (``TenantTask.apply_async``), and every request
the backend stores a row for carries that header. django-celery-results
writes ``request.meta`` into ``TaskResult.meta``; this backend puts the
schema there.

It reads the header, rather than having ``TenantTask.__call__`` set
``request.meta``, because that would miss a task killed at its HARD time
limit: the worker's parent process stores that row
(``celery.worker.request.Request.on_timeout``) from a request built off
the message, so ``TenantTask.__call__`` never ran for it. The header is
on every such request, and one mechanism covers a task that raised,
retried, hit its soft limit or its hard limit.

(A lost worker is not stored at all: ``CELERY_TASK_ACKS_LATE`` with
``CELERY_TASK_REJECT_ON_WORKER_LOST`` requeues the message instead, and the
rerun is stored, or not, by the worker that picks it up.)
"""

from __future__ import annotations

import json
from typing import Any

from celery import current_task
from django.db.models import Q
from django_celery_results.backends.database import DatabaseBackend
from django_celery_results.models import TaskResult
from django_tenants.utils import get_public_schema_name

#: The key under which the store's schema is kept in ``TaskResult.meta``.
SCHEMA_META_KEY = "schema_name"


def ran_in_store(schema_name: str) -> Q:
    """Rows whose ``meta`` says the task ran in ``schema_name``.

    ``meta`` is the JSON the backend wrote, kept in a text column, so
    "which store" is a substring match - on a table that holds only
    failures and is pruned after 30 days.
    """
    return Q(meta__contains=f'"{SCHEMA_META_KEY}": {json.dumps(schema_name)}')


def ran_on_platform() -> Q:
    """The public schema, or a row that records no store at all."""
    return ran_in_store(get_public_schema_name()) | ~Q(
        meta__contains=f'"{SCHEMA_META_KEY}"'
    )


def store_of(row: TaskResult) -> str | None:
    """The schema a stored task ran in, or ``None`` if its row says none."""
    try:
        return json.loads(row.meta or "{}").get(SCHEMA_META_KEY)
    except ValueError:
        return None


class TenantDatabaseBackend(DatabaseBackend):
    def _get_meta_from_request(self, request: Any = None) -> dict[str, Any]:
        meta = super()._get_meta_from_request(request)
        request = request or getattr(current_task, "request", None)
        schema_name = getattr(request, "_schema_name", None)
        if not schema_name:
            return meta
        return {**meta, SCHEMA_META_KEY: schema_name}
