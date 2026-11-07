"""A publish that fails after the change committed is kept, not lost.

``dispatch_on_commit`` runs after the transaction commits, so a broker that
cannot be reached can no longer undo anything: raising answers 500 for work
that was done. The dispatch is kept (``DeferredDispatch``) and
``redispatch_deferred_tasks`` publishes it again.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone
from kombu.exceptions import OperationalError

from tenant.celery import dispatch_on_commit, publish_or_drop
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


class TestAfterAFailedPublish:
    def test_the_next_one_is_kept_without_trying_the_broker(self):
        dispatch_on_commit(broker_down_task("first.task"), [1])
        second = broker_down_task("second.task")

        dispatch_on_commit(second, [2])

        second.apply_async.assert_not_called()
        kept = DeferredDispatch.objects.get(task_name="second.task")
        assert kept.args == [2]
        assert kept.last_error.startswith("BrokerKnownDown")

    def test_the_broker_is_tried_again_once_the_window_has_passed(
        self, settings
    ):
        dispatch_on_commit(broker_down_task(), [1])
        later = mock.Mock(name="later.task")

        with mock.patch(
            "tenant.celery.time.monotonic",
            return_value=time.monotonic()
            + settings.DEFERRED_DISPATCH_BROKER_DOWN_SECONDS
            + 1,
        ):
            dispatch_on_commit(later, [2])

        later.apply_async.assert_called_once()

    def test_a_bug_says_nothing_about_the_broker(self):
        broken = mock.Mock(name="broken.task")
        broken.apply_async.side_effect = TypeError("a bug")
        with pytest.raises(TypeError):
            dispatch_on_commit(broken, [1])
        healthy = mock.Mock(name="healthy.task")

        dispatch_on_commit(healthy, [2])

        healthy.apply_async.assert_called_once()

    def test_a_publish_that_works_leaves_the_next_to_try(self):
        first = mock.Mock(name="first.task")
        second = mock.Mock(name="second.task")

        dispatch_on_commit(first, [1])
        dispatch_on_commit(second, [2])

        second.apply_async.assert_called_once()


class TestThen:
    def test_it_runs_after_a_publish(self):
        then = mock.Mock()

        dispatch_on_commit(mock.Mock(), [1], then=then)

        then.assert_called_once_with()

    def test_it_runs_once_a_failed_publish_is_kept(self):
        then = mock.Mock()

        dispatch_on_commit(broker_down_task(), [1], then=then)

        assert DeferredDispatch.objects.count() == 1
        then.assert_called_once_with()

    def test_it_runs_once_a_publish_not_tried_is_kept(self):
        dispatch_on_commit(broker_down_task("first.task"), [1])
        then = mock.Mock()

        dispatch_on_commit(broker_down_task("second.task"), [2], then=then)

        assert DeferredDispatch.objects.count() == 2
        then.assert_called_once_with()

    def test_it_does_not_run_when_the_row_could_not_be_kept(self):
        then = mock.Mock()

        with mock.patch.object(
            DeferredDispatch.objects, "create", side_effect=RuntimeError
        ):
            dispatch_on_commit(broker_down_task(), [1], then=then)

        then.assert_not_called()

    def test_it_does_not_run_after_a_bug(self):
        broken = mock.Mock()
        broken.apply_async.side_effect = TypeError("a bug")
        then = mock.Mock()

        with pytest.raises(TypeError):
            dispatch_on_commit(broken, [1], then=then)

        then.assert_not_called()


def broker_down_beacon():
    task = mock.Mock()
    task.name = "beacon.task"
    task.delay.side_effect = OperationalError("broker unreachable")
    return task


class TestPublishOrDrop:
    def test_it_publishes_with_the_arguments_it_was_given(self):
        # `task` as a keyword: the search query has none, but nothing here
        # may take a task's argument for its own.
        beacon = mock.Mock()

        publish_or_drop(beacon, query="x", task="y")

        beacon.delay.assert_called_once_with(query="x", task="y")

    def test_a_broker_failure_is_logged_and_keeps_no_row(self, caplog):
        with caplog.at_level(logging.ERROR, logger="tenant.celery"):
            publish_or_drop(broker_down_beacon(), query="x")

        assert not DeferredDispatch.objects.exists()
        assert "Dropped beacon.task" in caplog.text

    def test_it_is_not_tried_once_a_publish_has_failed(self, caplog):
        dispatch_on_commit(broker_down_task(), [1])
        beacon = mock.Mock()
        caplog.clear()

        with caplog.at_level(logging.ERROR, logger="tenant.celery"):
            publish_or_drop(beacon, query="x")

        beacon.delay.assert_not_called()
        assert not caplog.records, "an outage must not log each beacon"

    def test_its_failure_spares_the_next_dispatch_the_wait(self):
        publish_or_drop(broker_down_beacon(), query="x")
        task = broker_down_task("next.task")

        dispatch_on_commit(task, [1])

        task.apply_async.assert_not_called()
        assert DeferredDispatch.objects.get().task_name == "next.task"

    def test_it_is_tried_again_once_the_window_has_passed(self, settings):
        publish_or_drop(broker_down_beacon(), query="x")
        later = mock.Mock()

        with mock.patch(
            "tenant.celery.time.monotonic",
            return_value=time.monotonic()
            + settings.DEFERRED_DISPATCH_BROKER_DOWN_SECONDS
            + 1,
        ):
            publish_or_drop(later, query="y")

        later.delay.assert_called_once_with(query="y")

    def test_a_bug_still_raises_and_says_nothing_about_the_broker(self):
        broken = mock.Mock()
        broken.delay.side_effect = TypeError("a bug")
        with pytest.raises(TypeError):
            publish_or_drop(broken)
        healthy = mock.Mock()

        publish_or_drop(healthy)

        healthy.delay.assert_called_once_with()


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
