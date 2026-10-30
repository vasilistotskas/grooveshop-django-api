"""A ``TenantTask`` runs in the store it was dispatched for.

``TenantTask.apply_async`` stamps the caller's schema into the message
as ``_schema_name`` and ``TenantTask.__call__`` binds that store before
the task body runs. A worker receives custom headers as attributes of
``Task.request``; an eagerly applied task (``CELERY_TASK_ALWAYS_EAGER``,
the test suite) keeps them under ``request.headers``. Both must bind.
"""

from __future__ import annotations

import pytest
from celery.app.task import Context
from django.db import connection

from core import celery_app
from tenant.celery import TenantTask
from tenant.models import Tenant
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)


@celery_app.task(base=TenantTask, name="tests.tenant_task_probe")
def probe_task() -> dict[str, object]:
    return {
        "schema": connection.schema_name,
        "is_row": isinstance(connection.tenant, Tenant),
        "pk": getattr(connection.tenant, "pk", None),
    }


@pytest.mark.django_db
class TestEagerTaskBindsItsStore:
    def test_runs_in_the_schema_it_was_dispatched_in(self):
        tenant = store_tenant("eager_bound_store")
        previous = bind_store_tenant(tenant)
        try:
            result = probe_task.apply_async().get()
        finally:
            unbind_store_tenant(previous)

        assert result == {
            "schema": "eager_bound_store",
            "is_row": True,
            "pk": tenant.pk,
        }

    def test_an_explicit_schema_header_wins(self):
        pinned = store_tenant("eager_pinned_store")

        result = probe_task.apply_async(
            headers={"_schema_name": pinned.schema_name}
        ).get()

        assert result["schema"] == "eager_pinned_store"
        assert result["pk"] == pinned.pk

    def test_without_a_header_the_task_runs_in_public(self):
        result = probe_task()

        assert result["schema"] == "public"


class TestWorkerRequestCarriesTheSchema:
    def test_a_worker_message_header_reaches_request_headers(self):
        # A worker builds ``Context`` from the message headers, where
        # custom keys sit at the top level.
        context = Context({"id": "x", "_schema_name": "worker_store"})

        assert context.headers == {"_schema_name": "worker_store"}
