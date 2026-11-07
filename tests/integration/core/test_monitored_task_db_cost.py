"""A task's completion line says what it cost the database.

``db_queries`` and ``db_ms`` (and ``duration_ms``) ride on the same
record as ``Task ... completed successfully`` and the failure line, so
a slow task can be told from a chatty one. The task runs through
Celery's own tracer (``.apply``), as in ``test_monitored_task_failure_log``.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.db import connection

from core.celery import celery_app
from core.logging import JsonFormatter
from core.tasks import MonitoredTask

pytestmark = pytest.mark.django_db

QUERIES = 4


@celery_app.task(base=MonitoredTask, name="tests.monitored_task_db_cost.runs")
def _runs_queries():
    for _ in range(QUERIES):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")


@celery_app.task(base=MonitoredTask, name="tests.monitored_task_db_cost.idle")
def _runs_none():
    return None


@celery_app.task(base=MonitoredTask, name="tests.monitored_task_db_cost.boom")
def _fails_after_a_query():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    raise ValueError("probe failure")


def _only_record(caplog) -> dict:
    (record,) = [r for r in caplog.records if r.name == "core.tasks"]
    return json.loads(JsonFormatter().format(record))


def test_the_completion_line_counts_the_task_queries(caplog):
    with caplog.at_level(logging.INFO, logger="core.tasks"):
        _runs_queries.apply()

    payload = _only_record(caplog)
    assert "completed successfully" in payload["message"]
    # The tenant lookup the base task makes is part of the task's cost.
    assert payload["db_queries"] >= QUERIES
    assert 0 < payload["db_ms"] <= payload["duration_ms"]


def test_a_task_with_no_query_logs_zero(caplog):
    with caplog.at_level(logging.INFO, logger="core.tasks"):
        _runs_none.apply()

    payload = _only_record(caplog)
    assert payload["db_queries"] == 0
    assert payload["db_ms"] == 0


def test_the_failure_line_carries_the_cost_too(caplog):
    with caplog.at_level(logging.INFO, logger="core.tasks"):
        _fails_after_a_query.apply(throw=False)

    payload = _only_record(caplog)
    assert payload["level"] == "ERROR"
    assert payload["db_queries"] >= 1
