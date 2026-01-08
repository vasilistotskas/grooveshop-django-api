"""
Query counting utilities for performance testing.

This module provides context managers and decorators for counting
database queries and asserting maximum query limits.

Usage:
    # As a context manager
    with QueryCountAssertion(max_queries=10) as counter:
        response = client.get('/api/products/')
    print(f"Executed {counter.count} queries")

    # As a decorator
    @assert_max_queries(15)
    def test_product_list(client):
        response = client.get('/api/products/')
        assert response.status_code == 200

    # Just counting queries
    with count_queries() as counter:
        Product.objects.for_list()[:10]
    print(f"Query count: {counter.count}")
"""

from contextlib import contextmanager
from functools import wraps
from typing import Callable, Optional

from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext


class QueryCountAssertion:
    """
    Context manager that counts database queries and optionally asserts a maximum.

    Attributes:
        max_queries: Maximum allowed queries (None to just count)
        count: Number of queries executed (available after exiting context)
        queries: List of query details (available after exiting context)

    Example:
        with QueryCountAssertion(max_queries=10) as counter:
            response = client.get('/api/products/')

        # Access results
        print(f"Queries: {counter.count}")
        for query in counter.queries:
            print(query['sql'])
    """

    def __init__(
        self,
        max_queries: Optional[int] = None,
        verbose: bool = False,
        fail_message: Optional[str] = None,
    ):
        """
        Initialize the query counter.

        Args:
            max_queries: Maximum allowed queries. If exceeded, raises AssertionError.
                        If None, just counts without asserting.
            verbose: If True, prints all queries when assertion fails.
            fail_message: Custom message to include in assertion error.
        """
        self.max_queries = max_queries
        self.verbose = verbose
        self.fail_message = fail_message
        self.count = 0
        self.queries: list = []
        self._context: Optional[CaptureQueriesContext] = None

    def __enter__(self) -> "QueryCountAssertion":
        reset_queries()
        self._context = CaptureQueriesContext(connection)
        self._context.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            self._context.__exit__(exc_type, exc_val, exc_tb)
            self.queries = list(self._context.captured_queries)
            self.count = len(self.queries)

        if exc_type is None and self.max_queries is not None:
            if self.count > self.max_queries:
                message = self._build_error_message()
                raise AssertionError(message)

        return False

    def _build_error_message(self) -> str:
        """Build a detailed error message for assertion failures."""
        parts = [
            f"Expected at most {self.max_queries} queries, "
            f"but {self.count} were executed."
        ]

        if self.fail_message:
            parts.insert(0, self.fail_message)

        if self.verbose and self.queries:
            parts.append("\nExecuted queries:")
            for i, query in enumerate(self.queries, 1):
                sql = query.get("sql", "")
                time = query.get("time", "?")
                parts.append(f"  {i}. [{time}s] {sql[:200]}...")

        return "\n".join(parts)

    def get_duplicate_queries(self) -> list:
        """
        Find duplicate queries (potential N+1 issues).

        Returns:
            List of (sql, count) tuples for queries executed more than once.
        """
        from collections import Counter

        sql_counts = Counter(q.get("sql", "") for q in self.queries)
        return [(sql, count) for sql, count in sql_counts.items() if count > 1]

    def get_slow_queries(self, threshold: float = 0.1) -> list:
        """
        Find queries slower than the threshold.

        Args:
            threshold: Time in seconds to consider a query slow.

        Returns:
            List of query dicts that exceeded the threshold.
        """
        return [q for q in self.queries if float(q.get("time", 0)) > threshold]


@contextmanager
def count_queries(verbose: bool = False):
    """
    Simple context manager to count queries without asserting.

    Args:
        verbose: If True, prints query count on exit.

    Yields:
        QueryCountAssertion instance with count and queries attributes.

    Example:
        with count_queries() as counter:
            list(Product.objects.all())
        print(f"Executed {counter.count} queries")
    """
    counter = QueryCountAssertion(max_queries=None, verbose=verbose)
    with counter:
        yield counter

    if verbose:
        print(f"Query count: {counter.count}")


def assert_max_queries(
    max_queries: int,
    verbose: bool = False,
    fail_message: Optional[str] = None,
) -> Callable:
    """
    Decorator to assert maximum query count for a test function.

    Args:
        max_queries: Maximum allowed queries.
        verbose: If True, prints all queries when assertion fails.
        fail_message: Custom message to include in assertion error.

    Returns:
        Decorated function that asserts query count.

    Example:
        @assert_max_queries(15)
        def test_product_list(client):
            response = client.get('/api/products/')
            assert response.status_code == 200

        @assert_max_queries(10, verbose=True, fail_message="Product list N+1")
        def test_product_detail(client, product):
            response = client.get(f'/api/products/{product.id}/')
            assert response.status_code == 200
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            with QueryCountAssertion(
                max_queries=max_queries,
                verbose=verbose,
                fail_message=fail_message or f"In {func.__name__}",
            ):
                return func(*args, **kwargs)

        return wrapper

    return decorator


# Performance thresholds for different endpoint types
QUERY_LIMITS = {
    "list": 15,
    "detail": 10,
    "action": 12,
    "create": 20,
    "update": 15,
    "delete": 10,
}


def get_query_limit(endpoint_type: str) -> int:
    """
    Get the recommended query limit for an endpoint type.

    Args:
        endpoint_type: One of 'list', 'detail', 'action', 'create', 'update', 'delete'

    Returns:
        Recommended maximum query count.
    """
    return QUERY_LIMITS.get(endpoint_type, 15)
