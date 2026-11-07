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
from django.db import connection, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from tenant.celery import dispatch_on_commit, publish_or_drop
from tenant.models import DeferredDispatch
from tenant.tasks import redispatch_deferred_tasks

pytestmark = pytest.mark.django_db


def broker_down_task(name: str = "demo.task"):
    task = mock.Mock()
    task.name = name
    task.sensitive_kwargs = frozenset()
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
        assert "42" in caplog.text

    def test_when_the_row_cannot_be_kept_a_secret_stays_out_of_the_log(
        self, caplog
    ):
        task = broker_down_task()
        task.sensitive_kwargs = frozenset({"context"})

        with (
            mock.patch.object(
                DeferredDispatch.objects, "create", side_effect=RuntimeError
            ),
            caplog.at_level(logging.ERROR, logger="tenant.celery"),
        ):
            dispatch_on_commit(
                task,
                kwargs={
                    "context": {"reset_url": "https://shop/reset/secret-link"},
                    "token": "secret-token",
                    "address": "shown@example.com",
                },
            )

        assert "secret-link" not in caplog.text
        assert "secret-token" not in caplog.text
        assert "shown@example.com" in caplog.text


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
        assert second.next_attempt_at <= timezone.now(), (
            "a row the run never reached is due again, not held by the lease"
        )

    def test_a_second_sweep_during_a_run_takes_nothing(self):
        deferred()
        task = mock.Mock()
        inner = []
        task.apply_async.side_effect = lambda **_: inner.append(
            redispatch_deferred_tasks()
        )

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task):
            outer = redispatch_deferred_tasks()

        assert outer["published"] == 1
        assert inner == [{"published": 0, "failed": 0, "expired": 0}]

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
        task = mock.Mock(sensitive_kwargs=frozenset())

        with (
            mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task),
            caplog.at_level(logging.ERROR, logger="tenant.tasks"),
        ):
            result = redispatch_deferred_tasks()

        assert result["expired"] == 1
        task.apply_async.assert_not_called()
        assert not DeferredDispatch.objects.exists()
        assert any("given up" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "task",
        [mock.Mock(sensitive_kwargs=frozenset({"context"})), None],
        ids=["a sensitive kwarg", "a task nobody knows"],
    )
    def test_a_row_given_up_keeps_its_secrets_out_of_the_log(
        self, caplog, settings, task
    ):
        row = deferred(age_days=settings.DEFERRED_DISPATCH_RETENTION_DAYS + 1)
        DeferredDispatch.objects.filter(pk=row.pk).update(
            kwargs={"context": {"reset_url": "https://shop/secret-link"}}
        )

        with (
            mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task),
            caplog.at_level(logging.ERROR, logger="tenant.tasks"),
        ):
            redispatch_deferred_tasks()

        assert "given up" in caplog.text
        assert "secret-link" not in caplog.text


@pytest.mark.django_db(transaction=True)
class TestAgainstARealCommit:
    def test_a_rolled_back_change_publishes_nothing_and_runs_nothing(self):
        task = mock.Mock()
        then = mock.Mock()

        with pytest.raises(RuntimeError), transaction.atomic():
            dispatch_on_commit(task, [1], then=then)
            raise RuntimeError

        task.apply_async.assert_not_called()
        then.assert_not_called()

    def test_the_publish_waits_for_the_commit_and_then_follows_it(self):
        calls = mock.Mock()

        with transaction.atomic():
            dispatch_on_commit(calls.task, [1], then=calls.then)
            assert calls.mock_calls == []

        assert [call[0] for call in calls.mock_calls] == [
            "task.apply_async",
            "then",
        ]

    def test_the_sweep_publishes_outside_any_transaction(self):
        deferred()
        in_transaction = []
        task = mock.Mock()
        task.apply_async.side_effect = lambda **_: in_transaction.append(
            connection.in_atomic_block
        )

        with mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task):
            redispatch_deferred_tasks()

        assert in_transaction == [False]

    def test_a_sweep_killed_half_way_sends_at_most_one_row_twice(self):
        class Killed(BaseException):
            pass

        first, second, third = deferred(), deferred(), deferred()
        task = mock.Mock()
        task.apply_async.side_effect = [None, Killed()]

        with (
            mock.patch("tenant.tasks.celery_app.tasks.get", return_value=task),
            pytest.raises(Killed),
        ):
            redispatch_deferred_tasks()

        assert not DeferredDispatch.objects.filter(pk=first.pk).exists(), (
            "a row that went out stays gone, whatever happens to the run"
        )
        second.refresh_from_db()
        third.refresh_from_db()
        assert second.next_attempt_at > timezone.now()
        assert third.next_attempt_at > timezone.now()
