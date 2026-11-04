"""A publish that fails after the change committed is kept, not lost.

``dispatch_on_commit`` runs after the transaction commits, so a broker that
cannot be reached can no longer undo anything: raising answers 500 for work
that was done. The dispatch is kept (``DeferredDispatch``) and
``redispatch_deferred_tasks`` publishes it again.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone
from kombu.exceptions import OperationalError

from tenant.celery import dispatch_on_commit
from tenant.models import DeferredDispatch
from tenant.tasks import redispatch_deferred_tasks

pytestmark = pytest.mark.django_db


def broker_down_task(name: str = "demo.task"):
    task = mock.Mock()
    task.name = name
    task.apply_async.side_effect = OperationalError("broker unreachable")
    return task


class TestAFailedPublish:
    def test_it_does_not_reach_the_caller_and_is_kept(self, caplog):
        task = broker_down_task()

        with caplog.at_level(logging.ERROR, logger="tenant.celery"):
            dispatch_on_commit(task, [7], {"x": 1}, countdown=30)

        row = DeferredDispatch.objects.get()
        assert row.task_name == "demo.task"
        assert (row.args, row.kwargs) == ([7], {"x": 1})
        assert row.headers == {"_schema_name": connection.schema_name}
        assert row.options == {"countdown": 30}
        assert row.schema_name == connection.schema_name
        assert "OperationalError" in row.last_error
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_a_bug_is_still_raised(self):
        task = mock.Mock()
        task.name = "demo.task"
        task.apply_async.side_effect = TypeError("bad arguments")

        with pytest.raises(TypeError):
            dispatch_on_commit(task, [1])

        assert not DeferredDispatch.objects.exists()

    def test_when_the_row_cannot_be_kept_the_payload_is_logged(self, caplog):
        task = broker_down_task()

        with (
            mock.patch.object(
                DeferredDispatch.objects, "create", side_effect=RuntimeError
            ),
            caplog.at_level(logging.ERROR, logger="tenant.celery"),
        ):
            dispatch_on_commit(task, [42])

        assert any("payload" in r.getMessage() for r in caplog.records)


def deferred(name="demo.task", *, age_days=0, due_in=timedelta(0), **fields):
    row = DeferredDispatch.objects.create(
        task_name=name,
        args=[1],
        kwargs={"k": 2},
        headers={"_schema_name": "shop"},
        options={"countdown": 5},
        schema_name="shop",
        next_attempt_at=timezone.now() + due_in,
        **fields,
    )
    if age_days:
        DeferredDispatch.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
    return row


class TestTheSweep:
    def test_a_row_is_published_with_what_it_was_given_and_deleted(self):
        row = deferred()
        task = mock.Mock()

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task):
            result = redispatch_deferred_tasks()

        assert result == {"published": 1, "failed": 0, "expired": 0}
        task.apply_async.assert_called_once_with(
            args=[1],
            kwargs={"k": 2},
            headers={"_schema_name": "shop"},
            countdown=5,
        )
        assert not DeferredDispatch.objects.filter(pk=row.pk).exists()

    def test_a_broker_still_down_backs_off_and_stops_the_run(self):
        first, second = deferred(), deferred()
        task = broker_down_task()

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task):
            result = redispatch_deferred_tasks()

        first.refresh_from_db()
        second.refresh_from_db()
        assert result["failed"] == 1
        assert task.apply_async.call_count == 1
        assert first.attempts == 1
        assert first.next_attempt_at > timezone.now()
        assert "OperationalError" in first.last_error
        assert second.attempts == 0

    def test_a_task_nobody_knows_is_kept_with_the_reason(self):
        row = deferred("nobody.knows")

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=None):
            result = redispatch_deferred_tasks()

        row.refresh_from_db()
        assert result["failed"] == 1
        assert "nobody.knows" in row.last_error

    def test_a_row_not_yet_due_is_left_alone(self):
        deferred(due_in=timedelta(hours=1))
        task = mock.Mock()

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task):
            redispatch_deferred_tasks()

        task.apply_async.assert_not_called()

    def test_a_row_older_than_the_retention_is_given_up_with_an_error(
        self, caplog, settings
    ):
        deferred(age_days=settings.DEFERRED_DISPATCH_RETENTION_DAYS + 1)
        task = mock.Mock()

        with (
            mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task),
            caplog.at_level(logging.ERROR, logger="tenant.tasks"),
        ):
            result = redispatch_deferred_tasks()

        assert result["expired"] == 1
        task.apply_async.assert_not_called()
        assert not DeferredDispatch.objects.exists()
        assert any("given up" in r.getMessage() for r in caplog.records)
