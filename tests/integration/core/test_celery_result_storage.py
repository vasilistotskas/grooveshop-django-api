"""Celery stores task FAILURES and nothing else.

Nothing in this codebase reads a task's return value — no
``AsyncResult``, no ``.get()``, no chord, no group. The one ``chain()``
(``core/tasks.py``) links an immutable ``.si()`` signature, which celery
dispatches from ``task_request.chain`` before it ever touches the
backend. So every stored success was write amplification nobody
consumed: 2,937 Redis keys a day in production, sharing one 614 MiB
``allkeys-lru`` budget with the Django cache.

The failures ARE worth keeping, durably and queryably: this platform has
no metrics or alerting stack, and a silent failure here is an order
confirmation nobody receives or a courier voucher that never mints.

Two settings make that split, and neither works alone:

* ``CELERY_TASK_IGNORE_RESULT`` — the producer stamps it onto every
  message (``Task.apply_async`` → ``options.setdefault('ignore_result',
  self.ignore_result)``), and the worker's success path then computes
  ``publish_result = not eager and not ignore_result``, so
  ``mark_as_done`` is called with ``store_result=False``.
* ``CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED`` — on the failure path
  ``TraceInfo.handle_error_state`` falls back to it *precisely* when a
  task is ignored, so the row is still written.

These tests drive ``build_tracer(..., eager=False)``, which is the
worker's own execution path, rather than ``.apply()``. That is not a
detail: ``Task.apply`` hard-codes ``'ignore_result': False`` into the
eager request, so an eager call can never observe this behaviour and a
test written that way would pass no matter what the settings said.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest
from celery.app.trace import build_tracer

from core.celery import celery_app

pytestmark = pytest.mark.django_db


@celery_app.task(name="tests.celery_result_storage.succeeds")
def _succeeds():
    return {"status": "ok"}


@celery_app.task(name="tests.celery_result_storage.fails")
def _fails():
    raise RuntimeError("boom")


def _run_like_a_worker(task) -> str:
    """Execute *task* through the tracer the worker itself builds.

    The request mirrors the protocol-2 message a worker hands the
    tracer: the ``ignore_result`` header the producer stamped on it
    (``Task.apply_async``), plus the fields django-celery-results reads
    for the extended columns — ``task`` becomes ``task_name``,
    ``hostname`` becomes ``worker``, ``argsrepr``/``kwargsrepr`` become
    the stored arguments.
    """
    task_id = str(uuid_module.uuid4())
    tracer = build_tracer(
        task.name, task, eager=False, propagate=False, app=celery_app
    )
    tracer(
        task_id,
        (),
        {},
        {
            "id": task_id,
            "task": task.name,
            "retries": 0,
            # Exactly what the producer puts on the message:
            # ``Task.apply_async`` does
            # ``options.setdefault('ignore_result', self.ignore_result)``.
            # Reading it off the task rather than hard-coding True is
            # what makes these tests fail if the setting is turned off.
            "ignore_result": task.ignore_result,
            "argsrepr": "()",
            "kwargsrepr": "{}",
            "hostname": "celery@test",
            "delivery_info": {},
        },
    )
    return task_id


@pytest.fixture
def task_results():
    from django.apps import apps

    return apps.get_model("django_celery_results", "TaskResult").objects


def test_a_successful_task_stores_nothing(task_results):
    task_id = _run_like_a_worker(_succeeds)

    assert not task_results.filter(task_id=task_id).exists(), (
        "a successful task wrote a result row — CELERY_TASK_IGNORE_RESULT "
        "is not reaching the worker, and every task is paying for storage "
        "that nothing in this codebase reads"
    )


def test_a_failing_task_stores_a_row_with_its_traceback(task_results):
    task_id = _run_like_a_worker(_fails)

    row = task_results.get(task_id=task_id)
    assert row.status == "FAILURE", (
        "a failing task did not record FAILURE — with results ignored, "
        "CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED is the only thing "
        "keeping failures on record, and the control plane has nothing "
        "to show"
    )
    assert "boom" in (row.traceback or ""), (
        "the failure row carries no traceback, which is the whole reason "
        "to keep it"
    )
    assert row.task_name == _fails.name, (
        "the row cannot be attributed to a task — CELERY_RESULT_EXTENDED "
        "is what puts the name and arguments on it, without which the "
        "admin lists opaque UUIDs"
    )


def test_the_producer_stamps_ignore_result_onto_every_message():
    """``apply_async`` copies the task's ``ignore_result`` onto the
    message, so the worker above is not being handed a value this
    codebase never sends."""
    assert _succeeds.ignore_result is True
    assert _succeeds.store_errors_even_if_ignored is True


def test_results_are_kept_long_enough_to_investigate_a_late_complaint():
    """Retention must be set under its post-4.0 name: the pre-4.0
    ``task_result_expires`` spelling is ignored under the ``CELERY_``
    namespace, which is how this ran on celery's 1-day default while
    settings.py claimed one hour. It is also what makes beat install
    ``celery.backend_cleanup``."""
    assert celery_app.conf.result_expires.days == 30
