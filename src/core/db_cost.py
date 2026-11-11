"""What a unit of work cost the database: query count and time spent in it.

An access line or a task line that carries only ``duration_ms`` says a
route is slow but not whether the time went into PostgreSQL, so the
slow routes could not be told apart from the chatty ones. ``measure``
wraps a request or a task in ``connection.execute_wrapper`` on every
configured alias and reports ``db_queries`` and ``db_ms``.

Django keeps one connection object per alias per thread, and the
wrapper is installed on that object. The ASGI handler runs sync
middleware, sync views and the ORM calls of async views on the same
thread-sensitive executor, so one ``measure`` around a request sees
every query that request runs, whatever its view flavour. A query a
view hands to another thread (``sync_to_async(thread_sensitive=False)``,
a worker pool) is not counted.

``executemany`` is one query, as in ``CaptureQueriesContext``. The time
is the driver call plus fetching nothing else: rows are fetched after
``execute`` returns, so it is a floor for a query that streams a large
result.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from django.db import connections


@dataclass(slots=True)
class DbCost:
    """Running totals of the queries one ``measure`` block saw."""

    queries: int = 0
    seconds: float = 0.0

    def __call__(
        self,
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.seconds += time.perf_counter() - started
            self.queries += 1

    def as_fields(self) -> dict[str, int | float]:
        """The log fields: ``db_queries`` and ``db_ms`` (1 decimal)."""
        return {
            "db_queries": self.queries,
            "db_ms": round(self.seconds * 1000, 1),
        }


@contextmanager
def measure() -> Generator[DbCost]:
    """Count and time every query on every DB alias inside the block.

    The totals are read after the block (or inside it, for a running
    figure); a failing query still counts, and the exception propagates.
    """
    cost = DbCost()
    with ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(cost))
        yield cost
