"""
Test utilities for query performance testing.

This module provides utilities for asserting query counts in tests,
helping ensure API endpoints don't have N+1 query problems.
"""

from tests.utils.query_counter import (
    QUERY_LIMITS,
    QueryCountAssertion,
    assert_max_queries,
    count_queries,
    get_query_limit,
)

__all__ = [
    "QUERY_LIMITS",
    "QueryCountAssertion",
    "assert_max_queries",
    "count_queries",
    "get_query_limit",
]
