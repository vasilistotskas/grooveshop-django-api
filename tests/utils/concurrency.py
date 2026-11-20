"""Calls racing on real database connections.

For ``django_db(transaction=True)`` tests: each call runs on a thread of its
own, so on a connection of its own. :func:`race` starts calls together;
:func:`in_thread` and :func:`wait_for_a_blocked_backend` order them, one
holding a row lock while another waits on it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from django.db import connection

# The test database lifts ``statement_timeout`` (tests/conftest.py), so a
# worker waiting on a lock would wait for ever. Each worker's connection
# gets bounds below the join deadline: a stuck statement fails the call,
# reported among the failures, instead of outliving the test.
WORKER_LOCK_TIMEOUT = "30s"
WORKER_STATEMENT_TIMEOUT = "60s"
START_TIMEOUT_SECONDS = 30
JOIN_TIMEOUT_SECONDS = 120


def race[T](
    call: Callable[[], T], *, workers: int = 2
) -> tuple[list[T], list[BaseException]]:
    """Run *call* on *workers* threads at once; their results and what
    they raised, each in the order the calls finished.

    A worker still running at the deadline fails the test. Its database
    session is terminated first, which ends its transaction and releases
    its locks, so the test database's teardown does not wait on it.
    """
    barrier = threading.Barrier(workers)
    results: list[T] = []
    failures: list[BaseException] = []
    backends: dict[threading.Thread, int] = {}

    def run() -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = %s", [WORKER_LOCK_TIMEOUT])
                cursor.execute(
                    "SET statement_timeout = %s", [WORKER_STATEMENT_TIMEOUT]
                )
                cursor.execute("SELECT pg_backend_pid()")
                backends[threading.current_thread()] = cursor.fetchone()[0]
            barrier.wait(timeout=START_TIMEOUT_SECONDS)
            results.append(call())
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, daemon=True) for _ in range(workers)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + JOIN_TIMEOUT_SECONDS
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    stuck = [thread for thread in threads if thread.is_alive()]
    if stuck:
        with connection.cursor() as cursor:
            for thread in stuck:
                if thread in backends:
                    cursor.execute(
                        "SELECT pg_terminate_backend(%s)", [backends[thread]]
                    )
        for thread in stuck:
            thread.join(timeout=START_TIMEOUT_SECONDS)
        pytest.fail(
            f"{len(stuck)} of {workers} racing calls still ran after "
            f"{JOIN_TIMEOUT_SECONDS} s; their sessions were terminated"
        )
    return results, failures


def in_thread(
    run: Callable[[], object], failures: list[BaseException]
) -> threading.Thread:
    """Start *run* on a thread of its own, so on a connection of its own;
    what it raises goes to *failures*. The caller joins the thread."""

    def target() -> None:
        try:
            run()
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=target)
    thread.start()
    return thread


def wait_for_a_blocked_backend(timeout: float) -> None:
    """Return once some connection of this database waits for a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.05)
    pytest.fail("no connection waited for a row lock")
