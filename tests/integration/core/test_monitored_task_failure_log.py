"""A failed task puts its traceback in the log at ERROR, once (#142).

``MonitoredTask.on_failure`` used to log the failure line at ERROR and
the traceback as a SECOND record at DEBUG. Production logs at INFO, so
the stack trace of a failed task never reached the log pipeline.

The task runs through Celery's own tracer (``.apply``), so ``on_failure``
receives exactly what a worker hands it: that is what exposed the trap
the handler guards against — ``einfo.exception`` is billiard's
``ExceptionWithTraceback`` wrapper, not the exception.
"""

from __future__ import annotations

import json
import logging

import pytest

from core.celery import celery_app
from core.logging import JsonFormatter
from core.tasks import MonitoredTask

pytestmark = pytest.mark.django_db

TASK_NAME = "tests.monitored_task_failure_log.fails"


def _raise_the_failure():
    raise ValueError("probe failure")


@celery_app.task(base=MonitoredTask, name=TASK_NAME)
def _fails(token=None):
    _raise_the_failure()


def _task_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "core.tasks"]


def test_a_failed_task_logs_one_error_record_carrying_the_traceback(caplog):
    with caplog.at_level(logging.DEBUG, logger="core.tasks"):
        result = _fails.apply(kwargs={"token": "hunter2"}, throw=False)

    assert result.failed()
    (record,) = _task_records(caplog)
    assert record.levelno == logging.ERROR
    exc_type, exc, tb = record.exc_info
    assert exc_type is ValueError
    assert str(exc) == "probe failure"
    assert tb is not None


def test_the_traceback_reaches_the_json_line_as_the_real_exception(caplog):
    with caplog.at_level(logging.INFO, logger="core.tasks"):
        _fails.apply(kwargs={"token": "hunter2"}, throw=False)

    (record,) = _task_records(caplog)
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["level"] == "ERROR"
    assert f"Task {TASK_NAME} failed." in payload["message"]
    assert "Traceback (most recent call last)" in payload["exception"]
    assert "_raise_the_failure" in payload["exception"]
    assert payload["exception"].rstrip().endswith("ValueError: probe failure")
    assert "ExceptionWithTraceback" not in payload["exception"]


def test_the_masked_kwargs_stay_masked(caplog):
    with caplog.at_level(logging.INFO, logger="core.tasks"):
        _fails.apply(kwargs={"token": "hunter2"}, throw=False)

    (record,) = _task_records(caplog)
    assert "hunter2" not in JsonFormatter().format(record)
