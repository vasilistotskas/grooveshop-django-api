"""A dispatch kept for a store lives in ``public`` and still names the store.

``DeferredDispatch`` is a shared table and the sweep that publishes it runs in
``public``, while the task and whatever ``then`` records belong to the store
that queued them. The main suite has one schema, so it cannot tell these apart.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import connection
from django_tenants.utils import tenant_context
from kombu.exceptions import OperationalError

from tenant.celery import dispatch_on_commit
from tenant.models import DeferredDispatch

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _broker_not_known_down(monkeypatch):
    monkeypatch.setattr("tenant.celery._broker_down_until", 0.0)


def test_a_row_kept_for_a_store_is_read_from_public_and_names_it(
    mt_tenant, django_capture_on_commit_callbacks
):
    task = mock.Mock()
    task.name = "demo.task"
    task.apply_async.side_effect = OperationalError("broker unreachable")

    with (
        django_capture_on_commit_callbacks(execute=True),
        tenant_context(mt_tenant),
    ):
        dispatch_on_commit(task, [1])

    assert connection.schema_name == "public"
    row = DeferredDispatch.objects.get(task_name="demo.task")
    assert row.schema_name == mt_tenant.schema_name
    assert row.headers == {"_schema_name": mt_tenant.schema_name}


def test_then_runs_in_the_store_that_queued_it_once_the_context_has_unwound(
    mt_tenant, django_capture_on_commit_callbacks
):
    seen = []

    with django_capture_on_commit_callbacks(execute=True):
        with tenant_context(mt_tenant):
            dispatch_on_commit(
                mock.Mock(),
                [1],
                then=lambda: seen.append(connection.schema_name),
            )
        assert connection.schema_name == "public"

    assert seen == [mt_tenant.schema_name]
    assert connection.schema_name == "public"
