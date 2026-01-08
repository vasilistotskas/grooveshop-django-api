#!/usr/bin/env python
"""
Debug script for analyzing query performance across all API endpoints.

Usage:
    uv run python debug_queries.py [--endpoint ENDPOINT] [--verbose]

Examples:
    uv run python debug_queries.py                    # Test all endpoints
    uv run python debug_queries.py --endpoint blog   # Test only blog endpoints
    uv run python debug_queries.py --verbose         # Show all queries
"""

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Optional

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

import django

django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries  # noqa: E402

# Enable debug mode for query logging
settings.DEBUG = True


@dataclass
class QueryResult:
    """Result of a query performance test."""

    name: str
    query_count: int
    max_allowed: int
    passed: bool
    queries: list
    error: Optional[str] = None


# Query limits for different endpoint types
QUERY_LIMITS = {
    "list": 15,
    "detail": 10,
    "action": 12,
}


def count_queries(func):
    """Decorator to count queries for a function."""
    reset_queries()
    try:
        func()
        return len(connection.queries), list(connection.queries), None
    except Exception as e:
        return 0, [], str(e)


def test_blog_post_list() -> QueryResult:
    """Test BlogPost list endpoint queries."""
    from blog.models.post import BlogPost

    reset_queries()
    try:
        queryset = BlogPost.objects.for_list()[:10]
        posts = list(queryset)
        for post in posts:
            _ = post.likes_count
            _ = post.comments_count
            _ = post.tags_count

        query_count = len(connection.queries)
        return QueryResult(
            name="BlogPost List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="BlogPost List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_blog_category_list() -> QueryResult:
    """Test BlogCategory list endpoint queries."""
    from blog.models.category import BlogCategory

    reset_queries()
    try:
        queryset = BlogCategory.objects.for_list()[:10]
        categories = list(queryset)
        for cat in categories:
            _ = str(cat)

        query_count = len(connection.queries)
        return QueryResult(
            name="BlogCategory List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="BlogCategory List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_product_list() -> QueryResult:
    """Test Product list endpoint queries."""
    from product.models.product import Product

    reset_queries()
    try:
        queryset = Product.objects.for_list()[:10]
        products = list(queryset)
        for product in products:
            _ = product.likes_count
            _ = product.review_average

        query_count = len(connection.queries)
        return QueryResult(
            name="Product List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="Product List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_product_category_list() -> QueryResult:
    """Test ProductCategory list endpoint queries."""
    from product.models.category import ProductCategory

    reset_queries()
    try:
        queryset = ProductCategory.objects.for_list()[:10]
        categories = list(queryset)
        for cat in categories:
            _ = str(cat)

        query_count = len(connection.queries)
        return QueryResult(
            name="ProductCategory List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="ProductCategory List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_order_list() -> QueryResult:
    """Test Order list endpoint queries."""
    from order.models.order import Order

    reset_queries()
    try:
        queryset = Order.objects.for_list()[:10]
        orders = list(queryset)
        for order in orders:
            _ = order.items_count
            _ = order.total_quantity

        query_count = len(connection.queries)
        return QueryResult(
            name="Order List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="Order List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_cart_list() -> QueryResult:
    """Test Cart list endpoint queries."""
    from cart.models.cart import Cart

    reset_queries()
    try:
        queryset = Cart.objects.for_list()[:10]
        carts = list(queryset)
        for cart in carts:
            _ = str(cart)

        query_count = len(connection.queries)
        return QueryResult(
            name="Cart List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="Cart List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_notification_list() -> QueryResult:
    """Test NotificationUser list endpoint queries."""
    from notification.models.user import NotificationUser

    reset_queries()
    try:
        queryset = NotificationUser.objects.for_list()[:10]
        notifications = list(queryset)
        for notif in notifications:
            _ = str(notif)

        query_count = len(connection.queries)
        return QueryResult(
            name="NotificationUser List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="NotificationUser List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_country_list() -> QueryResult:
    """Test Country list endpoint queries."""
    from country.models import Country

    reset_queries()
    try:
        queryset = Country.objects.for_list()[:10]
        countries = list(queryset)
        for country in countries:
            _ = str(country)

        query_count = len(connection.queries)
        return QueryResult(
            name="Country List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="Country List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_region_list() -> QueryResult:
    """Test Region list endpoint queries."""
    from region.models import Region

    reset_queries()
    try:
        queryset = Region.objects.for_list()[:10]
        regions = list(queryset)
        for region in regions:
            _ = str(region)

        query_count = len(connection.queries)
        return QueryResult(
            name="Region List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="Region List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


def test_pay_way_list() -> QueryResult:
    """Test PayWay list endpoint queries."""
    from pay_way.models import PayWay

    reset_queries()
    try:
        queryset = PayWay.objects.for_list()[:10]
        pay_ways = list(queryset)
        for pw in pay_ways:
            _ = str(pw)

        query_count = len(connection.queries)
        return QueryResult(
            name="PayWay List",
            query_count=query_count,
            max_allowed=QUERY_LIMITS["list"],
            passed=query_count <= QUERY_LIMITS["list"],
            queries=list(connection.queries),
        )
    except Exception as e:
        return QueryResult(
            name="PayWay List",
            query_count=0,
            max_allowed=QUERY_LIMITS["list"],
            passed=False,
            queries=[],
            error=str(e),
        )


# All test functions grouped by category
ALL_TESTS = {
    "blog": [test_blog_post_list, test_blog_category_list],
    "product": [test_product_list, test_product_category_list],
    "order": [test_order_list],
    "cart": [test_cart_list],
    "notification": [test_notification_list],
    "country": [test_country_list],
    "region": [test_region_list],
    "pay_way": [test_pay_way_list],
}


def print_result(result: QueryResult, verbose: bool = False):
    """Print a single test result."""
    status = "PASS" if result.passed else "FAIL"
    marker = "[+]" if result.passed else "[X]"

    print(
        f"{marker} {status} {result.name}: {result.query_count}/{result.max_allowed} queries"
    )

    if result.error:
        print(f"       Error: {result.error}")

    if verbose and result.queries:
        print("       Queries:")
        for i, q in enumerate(result.queries, 1):
            time = q.get("time", "?")
            sql = q.get("sql", "")[:100]
            print(f"         {i}. [{time}s] {sql}...")


def print_summary(results: list[QueryResult]):
    """Print summary of all test results."""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.query_count}/{r.max_allowed} queries")
                if r.error:
                    print(f"    Error: {r.error}")

    print("=" * 60)

    return failed == 0


def main():
    parser = argparse.ArgumentParser(
        description="Debug query performance for API endpoints"
    )
    parser.add_argument(
        "--endpoint",
        "-e",
        choices=list(ALL_TESTS.keys()) + ["all"],
        default="all",
        help="Endpoint category to test (default: all)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all queries for each test",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("QUERY PERFORMANCE AUDIT")
    print("=" * 60)
    print(
        f"Query Limits: list={QUERY_LIMITS['list']}, detail={QUERY_LIMITS['detail']}, action={QUERY_LIMITS['action']}"
    )
    print("=" * 60 + "\n")

    results = []

    if args.endpoint == "all":
        for category, tests in ALL_TESTS.items():
            print(f"\n{category.upper()}")
            print("-" * 40)
            for test_func in tests:
                result = test_func()
                results.append(result)
                print_result(result, args.verbose)
    else:
        tests = ALL_TESTS.get(args.endpoint, [])
        print(f"\n{args.endpoint.upper()}")
        print("-" * 40)
        for test_func in tests:
            result = test_func()
            results.append(result)
            print_result(result, args.verbose)

    success = print_summary(results)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
