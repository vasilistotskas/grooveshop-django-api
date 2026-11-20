"""A ``TenantTask`` runs in the store it was dispatched for.

``TenantTask.apply_async`` stamps the caller's schema into the message
as ``_schema_name`` and ``TenantTask.__call__`` binds that store before
the task body runs. A worker receives custom headers as attributes of
``Task.request``; an eagerly applied task (``CELERY_TASK_ALWAYS_EAGER``,
the test suite) keeps them under ``request.headers``. Both must bind.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import psycopg
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


def _store_locks_held(schema_name: str) -> int:
    """Shared advisory locks any session holds on the store's key."""
    from tenant.celery import _store_lock_key

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND mode = 'ShareLock' AND granted "
            "AND ((classid::bigint << 32) | objid::bigint) "
            "= hashtextextended(%s, 0)",
            [_store_lock_key(schema_name)],
        )
        (count,) = cursor.fetchone()
    return count


@celery_app.task(base=TenantTask, name="tests.held_store_lock_probe")
def held_store_lock_probe() -> int:
    return _store_locks_held(connection.schema_name)


@celery_app.task(base=TenantTask, name="tests.closing_store_lock_probe")
def closing_store_lock_probe() -> int:
    """Closes its connection mid-run, as ``meilisearch_sync_all_indexes``
    does, then goes on querying the store."""
    connection.close()
    return _store_locks_held(connection.schema_name)


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

    def test_a_task_whose_store_was_destroyed_does_not_run(self):
        """Queued for a store whose destroy committed before a worker took
        it: its schema is gone, so the search path would fall through to
        public's tables."""
        from django.utils import timezone

        from tenant.models import TenantArchive

        TenantArchive.objects.create(
            schema_name="destroyed_store",
            tenant_name="Destroyed",
            destroyed_at=timezone.now(),
        )

        result = probe_task.apply_async(
            headers={"_schema_name": "destroyed_store"}
        ).get()

        assert result is None

    def test_the_task_holds_its_store_while_it_runs(self):
        """A destroy waits for it (``tenant.celery.holding_store``)."""
        tenant = store_tenant("eager_held_store")

        held = held_store_lock_probe.apply_async(
            headers={"_schema_name": tenant.schema_name}
        ).get()

        assert held == 1

    def test_a_schema_with_no_store_row_still_runs_bare(self):
        result = probe_task.apply_async(
            headers={"_schema_name": "rowless_schema"}
        ).get()

        assert result == {
            "schema": "rowless_schema",
            "is_row": False,
            "pk": None,
        }


@pytest.mark.django_db(transaction=True)
class TestOutsideATransaction:
    """A worker runs a task in autocommit: the lock is held on a session of
    its own for the whole task and released when the task ends."""

    def test_the_store_is_held_only_while_the_task_runs(self):
        tenant = store_tenant("worker_held_store")

        held = held_store_lock_probe.apply_async(
            headers={"_schema_name": tenant.schema_name}
        ).get()

        assert held == 1
        assert _store_locks_held(tenant.schema_name) == 0

    def test_closing_the_task_connection_keeps_the_store_held(self):
        """The task's own connection can end mid-run without opening a gap
        in which a destroy could drop the schema under it."""
        tenant = store_tenant("worker_closing_store")

        held = closing_store_lock_probe.apply_async(
            headers={"_schema_name": tenant.schema_name}
        ).get()

        assert held == 1
        assert _store_locks_held(tenant.schema_name) == 0

    @pytest.mark.parametrize(
        "timeouts",
        ["-c statement_timeout=300", "-c lock_timeout=300"],
        ids=["statement_timeout", "lock_timeout"],
    )
    def test_a_task_waits_out_a_destroy_past_the_connection_timeouts(
        self, timeouts
    ):
        """A task that starts while a destroy holds the store waits for it,
        however long the destroy takes, instead of failing on the worker
        connection's timeouts (``statement_timeout`` is 30 s there)."""
        from tenant.celery import _store_lock_key

        tenant = store_tenant("worker_waits_store")
        params = connection.get_connection_params()
        params["autocommit"] = True
        options = {**connection.settings_dict["OPTIONS"], "options": timeouts}

        with psycopg.connect(**params) as destroy:
            destroy.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                [_store_lock_key(tenant.schema_name)],
            )
            commit = threading.Timer(
                1.0, destroy.execute, ["SELECT pg_advisory_unlock_all()"]
            )
            connection.close()
            with patch.dict(connection.settings_dict, {"OPTIONS": options}):
                commit.start()
                try:
                    held = held_store_lock_probe.apply_async(
                        headers={"_schema_name": tenant.schema_name}
                    ).get()
                finally:
                    commit.join()
                    connection.close()

        assert held == 1


class TestWorkerRequestCarriesTheSchema:
    def test_a_worker_message_header_reaches_request_headers(self):
        # A worker builds ``Context`` from the message headers, where
        # custom keys sit at the top level.
        context = Context({"id": "x", "_schema_name": "worker_store"})

        assert context.headers == {"_schema_name": "worker_store"}
