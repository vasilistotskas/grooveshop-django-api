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


def _bounded_session() -> int:
    """Bound this thread's database session below the join deadlines
    (``lock_timeout``, ``statement_timeout``) and return its backend pid,
    so a worker still running at its deadline can be terminated."""
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = %s", [WORKER_LOCK_TIMEOUT])
        cursor.execute("SET statement_timeout = %s", [WORKER_STATEMENT_TIMEOUT])
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _terminate(
    threads: list[threading.Thread], backends: dict[threading.Thread, int]
) -> None:
    """End the sessions of *threads*, still running: their transactions
    end and their locks go, so the test database's teardown does not wait
    on them; then give each a moment to finish."""
    with connection.cursor() as cursor:
        for thread in threads:
            if thread in backends:
                cursor.execute(
                    "SELECT pg_terminate_backend(%s)", [backends[thread]]
                )
    for thread in threads:
        thread.join(timeout=START_TIMEOUT_SECONDS)


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
            backends[threading.current_thread()] = _bounded_session()
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
        _terminate(stuck, backends)
        pytest.fail(
            f"{len(stuck)} of {workers} racing calls still ran after "
            f"{JOIN_TIMEOUT_SECONDS} s; their sessions were terminated"
        )
    return results, failures


class Worker:
    """A call running on a thread, and a database session, of its own
    (:func:`in_thread`)."""

    def __init__(
        self, thread: threading.Thread, backends: dict[threading.Thread, int]
    ):
        self._thread = thread
        self._backends = backends

    def finish(self, timeout: float) -> None:
        """Wait up to *timeout* seconds for the call to end. One still
        running fails the test, its session terminated first."""
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            _terminate([self._thread], self._backends)
            pytest.fail(
                f"a call on its own connection still ran after {timeout} s; "
                "its session was terminated"
            )


def in_thread(
    run: Callable[[], object], failures: list[BaseException]
) -> Worker:
    """Start *run* on a thread of its own, so on a session of its own,
    bounded like a :func:`race` worker (a daemon thread whose lock and
    statement waits time out); what it raises goes to *failures*. The
    caller ends it with :meth:`Worker.finish`."""
    backends: dict[threading.Thread, int] = {}

    def target() -> None:
        try:
            backends[threading.current_thread()] = _bounded_session()
            run()
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return Worker(thread, backends)


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
