"""No task may retry on ``Exception``.

``autoretry_for=(Exception,)`` retries a ``TypeError`` or an
``IntegrityError`` exactly as often as an outage. Each attempt fails the
same way, so the retries only delay the failure record, and a task with
side effects repeats them. 63 tasks carried it, 3 of them through the
``MeiliTask`` class default rather than a decorator.

The walk reads ``autoretry_for`` off every registered task instance, so a
class-level default is caught as well as a decorator argument.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock

import pytest
import requests
from celery import Task
from django.db.utils import IntegrityError, OperationalError

from core import celery_app
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    MEILI_TRANSIENT_ERRORS,
    TRANSIENT_ERRORS,
    TRANSIENT_HTTP_ERRORS,
    is_permanent_failure,
    is_permanent_http_failure,
)
from core.tasks import MonitoredTask
from meili.exceptions import MeiliTaskFailed
from meili.tasks import MeiliTask

# A floor, not a count: it only has to prove the walk saw the project's
# tasks and not an empty registry.
_MINIMUM_PROJECT_TASKS = 50


def _project_tasks() -> list[Task]:
    celery_app.autodiscover_tasks(force=True)
    return [
        task
        for name, task in celery_app.tasks.items()
        if not name.startswith("celery.")
    ]


def test_the_walk_sees_the_projects_tasks():
    assert len(_project_tasks()) >= _MINIMUM_PROJECT_TASKS


def test_no_task_autoretries_on_exception():
    offenders = sorted(
        task.name
        for task in _project_tasks()
        if any(
            issubclass(Exception, retry_for)
            for retry_for in tuple(getattr(task, "autoretry_for", ()))
        )
    )

    assert not offenders, (
        "These tasks retry on Exception, so a bug is retried like an "
        "outage. Name a set from core.retry instead:\n  "
        + "\n  ".join(offenders)
    )


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


class TestHttpVerdict:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_a_client_error_is_permanent(self, status):
        assert is_permanent_http_failure(_http_error(status)) is True

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
    def test_a_throttle_or_server_error_is_retried(self, status):
        assert is_permanent_http_failure(_http_error(status)) is False

    def test_an_http_error_without_a_response_is_retried(self):
        assert is_permanent_http_failure(requests.HTTPError("boom")) is False

    def test_a_connection_failure_is_retried(self):
        assert is_permanent_failure(requests.ConnectionError("down")) is False


class TestMonitoredTaskRetry:
    """``autoretry_for`` and an explicit ``self.retry`` both land here."""

    @pytest.mark.parametrize(
        "exc",
        [
            smtplib.SMTPAuthenticationError(535, b"bad creds"),
            _http_error(404),
        ],
    )
    def test_a_permanent_failure_is_raised_not_rescheduled(self, exc):
        task = MonitoredTask()
        with pytest.raises(type(exc)):
            task.retry(exc=exc)

    def test_a_transient_failure_is_rescheduled(self, monkeypatch):
        forward = MagicMock(side_effect=RuntimeError("rescheduled"))
        monkeypatch.setattr(Task, "retry", forward)

        with pytest.raises(RuntimeError, match="rescheduled"):
            MonitoredTask().retry(exc=smtplib.SMTPConnectError(421, b"busy"))

        assert forward.called


class TestTheNamedSets:
    def test_an_integrity_error_is_never_transient(self):
        assert not issubclass(IntegrityError, TRANSIENT_ERRORS)
        assert issubclass(OperationalError, TRANSIENT_ERRORS)

    def test_mail_keeps_every_smtp_failure_retryable(self):
        assert issubclass(smtplib.SMTPServerDisconnected, MAIL_TRANSIENT_ERRORS)
        assert issubclass(smtplib.SMTPConnectError, MAIL_TRANSIENT_ERRORS)
        assert issubclass(OperationalError, MAIL_TRANSIENT_ERRORS)

    def test_meili_retries_an_outage_but_not_a_rejected_write(self):
        retry_for = tuple(MeiliTask.autoretry_for)
        assert not issubclass(MeiliTaskFailed, retry_for)
        assert all(
            issubclass(error, retry_for) for error in MEILI_TRANSIENT_ERRORS
        )

    def test_http_set_covers_raise_for_status(self):
        assert issubclass(requests.HTTPError, TRANSIENT_HTTP_ERRORS)
