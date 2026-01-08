#!/usr/bin/env python
"""
Comprehensive API Query Performance Audit Script.

This script tests all API endpoints automatically and generates a detailed
report with recommendations for optimization.

Usage:
    uv run python scripts/audit_queries.py [--output FILE] [--format FORMAT]

Examples:
    uv run python scripts/audit_queries.py                      # Run audit, print to console
    uv run python scripts/audit_queries.py --output report.json # Save JSON report
    uv run python scripts/audit_queries.py --format markdown    # Output as markdown

Features:
    - Tests all list, detail, and action endpoints
    - Measures query counts and execution times
    - Identifies N+1 query patterns
    - Generates recommendations for optimization
    - Supports multiple output formats (console, JSON, markdown)
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

import django

django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries  # noqa: E402

# Enable debug mode for query logging
settings.DEBUG = True


# Query limits for different endpoint types
QUERY_LIMITS = {
    "list": 15,
    "detail": 10,
    "action": 12,
}


@dataclass
class QueryInfo:
    """Information about a single database query."""

    sql: str
    time: float
    is_duplicate: bool = False


@dataclass
class EndpointResult:
    """Result of testing a single endpoint."""

    name: str
    category: str
    endpoint_type: str  # list, detail, action
    query_count: int
    max_allowed: int
    passed: bool
    execution_time_ms: float
    queries: list[QueryInfo] = field(default_factory=list)
    duplicate_queries: int = 0
    error: str | None = None
    recommendations: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """Complete audit report."""

    timestamp: str
    total_endpoints: int
    passed_endpoints: int
    failed_endpoints: int
    total_queries: int
    duplicate_queries: int
    results: list[EndpointResult]
    summary: dict[str, Any] = field(default_factory=dict)


def analyze_queries(queries: list[dict]) -> tuple[list[QueryInfo], int]:
    """Analyze queries for duplicates and patterns."""
    query_infos = []
    seen_queries = {}
    duplicate_count = 0

    for q in queries:
        sql = q.get("sql", "")
        time_taken = float(q.get("time", 0))

        # Normalize SQL for duplicate detection (remove specific IDs)
        normalized = sql
        for char in "0123456789":
            normalized = normalized.replace(char, "X")

        is_duplicate = normalized in seen_queries
        if is_duplicate:
            duplicate_count += 1
        else:
            seen_queries[normalized] = True

        query_infos.append(
            QueryInfo(sql=sql[:200], time=time_taken, is_duplicate=is_duplicate)
        )

    return query_infos, duplicate_count


def generate_recommendations(result: EndpointResult) -> list[str]:
    """Generate optimization recommendations based on results."""
    recommendations = []

    if result.duplicate_queries > 0:
        recommendations.append(
            f"Found {result.duplicate_queries} duplicate queries. "
            "Consider using select_related() or prefetch_related()."
        )

    if result.query_count > result.max_allowed:
        excess = result.query_count - result.max_allowed
        recommendations.append(
            f"Exceeds limit by {excess} queries. "
            "Review queryset optimization in ViewSet.get_queryset()."
        )

    if result.execution_time_ms > 500:
        recommendations.append(
            f"Slow execution ({result.execution_time_ms:.0f}ms). "
            "Consider adding database indexes or caching."
        )

    # Check for common N+1 patterns in queries
    select_count = sum(1 for q in result.queries if "SELECT" in q.sql.upper())
    if select_count > 5 and result.endpoint_type == "list":
        recommendations.append(
            f"High SELECT count ({select_count}) for list endpoint. "
            "Likely N+1 pattern - use prefetch_related() for related data."
        )

    return recommendations


def run_test(
    name: str,
    category: str,
    endpoint_type: str,
    test_func,
) -> EndpointResult:
    """Run a single endpoint test."""
    reset_queries()
    start_time = time.time()

    try:
        test_func()
        execution_time = (time.time() - start_time) * 1000
        queries = list(connection.queries)
        query_infos, duplicate_count = analyze_queries(queries)

        max_allowed = QUERY_LIMITS.get(endpoint_type, 15)
        query_count = len(queries)

        result = EndpointResult(
            name=name,
            category=category,
            endpoint_type=endpoint_type,
            query_count=query_count,
            max_allowed=max_allowed,
            passed=query_count <= max_allowed,
            execution_time_ms=execution_time,
            queries=query_infos,
            duplicate_queries=duplicate_count,
        )

        result.recommendations = generate_recommendations(result)
        return result

    except Exception as e:
        return EndpointResult(
            name=name,
            category=category,
            endpoint_type=endpoint_type,
            query_count=0,
            max_allowed=QUERY_LIMITS.get(endpoint_type, 15),
            passed=False,
            execution_time_ms=0,
            error=str(e),
            recommendations=[f"Fix error: {e}"],
        )


# Test functions for each endpoint
def test_blog_post_list():
    from blog.models.post import BlogPost

    queryset = BlogPost.objects.for_list()[:10]
    posts = list(queryset)
    for post in posts:
        _ = post.likes_count
        _ = post.comments_count
        _ = post.tags_count


def test_blog_post_detail():
    from blog.models.post import BlogPost

    post = BlogPost.objects.for_detail().first()
    if post:
        _ = post.likes_count
        _ = post.comments_count
        _ = list(post.tags.all())


def test_blog_category_list():
    from blog.models.category import BlogCategory

    queryset = BlogCategory.objects.for_list()[:10]
    categories = list(queryset)
    for cat in categories:
        _ = str(cat)


def test_blog_comment_list():
    from blog.models.comment import BlogComment

    queryset = BlogComment.objects.for_list()[:10]
    comments = list(queryset)
    for comment in comments:
        _ = str(comment)


def test_blog_author_list():
    from blog.models.author import BlogAuthor

    queryset = BlogAuthor.objects.for_list()[:10]
    authors = list(queryset)
    for author in authors:
        _ = str(author)


def test_blog_tag_list():
    from blog.models.tag import BlogTag

    queryset = BlogTag.objects.for_list()[:10]
    tags = list(queryset)
    for tag in tags:
        _ = str(tag)


def test_product_list():
    from product.models.product import Product

    queryset = Product.objects.for_list()[:10]
    products = list(queryset)
    for product in products:
        _ = product.likes_count
        _ = product.review_average


def test_product_detail():
    from product.models.product import Product

    product = Product.objects.for_detail().first()
    if product:
        _ = product.likes_count
        _ = product.review_average
        _ = list(product.images.all())


def test_product_category_list():
    from product.models.category import ProductCategory

    queryset = ProductCategory.objects.for_list()[:10]
    categories = list(queryset)
    for cat in categories:
        _ = str(cat)


def test_product_review_list():
    from product.models.review import ProductReview

    queryset = ProductReview.objects.for_list()[:10]
    reviews = list(queryset)
    for review in reviews:
        _ = str(review)


def test_product_image_list():
    from product.models.image import ProductImage

    queryset = ProductImage.objects.for_list()[:10]
    images = list(queryset)
    for image in images:
        _ = str(image)


def test_product_favourite_list():
    from product.models.favourite import ProductFavourite

    queryset = ProductFavourite.objects.for_list()[:10]
    favourites = list(queryset)
    for fav in favourites:
        _ = str(fav)


def test_order_list():
    from order.models.order import Order

    queryset = Order.objects.for_list()[:10]
    orders = list(queryset)
    for order in orders:
        _ = order.items_count
        _ = order.total_quantity


def test_order_detail():
    from order.models.order import Order

    order = Order.objects.for_detail().first()
    if order:
        _ = order.items_count
        _ = list(order.items.all())


def test_cart_list():
    from cart.models.cart import Cart

    queryset = Cart.objects.for_list()[:10]
    carts = list(queryset)
    for cart in carts:
        _ = str(cart)


def test_cart_detail():
    from cart.models.cart import Cart

    cart = Cart.objects.for_detail().first()
    if cart:
        _ = list(cart.items.all())


def test_notification_list():
    from notification.models.user import NotificationUser

    queryset = NotificationUser.objects.for_list()[:10]
    notifications = list(queryset)
    for notif in notifications:
        _ = str(notif)


def test_country_list():
    from country.models import Country

    queryset = Country.objects.for_list()[:10]
    countries = list(queryset)
    for country in countries:
        _ = str(country)


def test_region_list():
    from region.models import Region

    queryset = Region.objects.for_list()[:10]
    regions = list(queryset)
    for region in regions:
        _ = str(region)


def test_pay_way_list():
    from pay_way.models import PayWay

    queryset = PayWay.objects.for_list()[:10]
    pay_ways = list(queryset)
    for pw in pay_ways:
        _ = str(pw)


def test_tag_list():
    from tag.models.tag import Tag

    queryset = Tag.objects.for_list()[:10]
    tags = list(queryset)
    for tag in tags:
        _ = str(tag)


def test_user_address_list():
    from user.models.address import UserAddress

    queryset = UserAddress.objects.for_list()[:10]
    addresses = list(queryset)
    for addr in addresses:
        _ = str(addr)


# All tests organized by category
ALL_TESTS = [
    # Blog
    ("BlogPost List", "blog", "list", test_blog_post_list),
    ("BlogPost Detail", "blog", "detail", test_blog_post_detail),
    ("BlogCategory List", "blog", "list", test_blog_category_list),
    ("BlogComment List", "blog", "list", test_blog_comment_list),
    ("BlogAuthor List", "blog", "list", test_blog_author_list),
    ("BlogTag List", "blog", "list", test_blog_tag_list),
    # Product
    ("Product List", "product", "list", test_product_list),
    ("Product Detail", "product", "detail", test_product_detail),
    ("ProductCategory List", "product", "list", test_product_category_list),
    ("ProductReview List", "product", "list", test_product_review_list),
    ("ProductImage List", "product", "list", test_product_image_list),
    ("ProductFavourite List", "product", "list", test_product_favourite_list),
    # Order
    ("Order List", "order", "list", test_order_list),
    ("Order Detail", "order", "detail", test_order_detail),
    # Cart
    ("Cart List", "cart", "list", test_cart_list),
    ("Cart Detail", "cart", "detail", test_cart_detail),
    # Notification
    ("NotificationUser List", "notification", "list", test_notification_list),
    # Supporting
    ("Country List", "country", "list", test_country_list),
    ("Region List", "region", "list", test_region_list),
    ("PayWay List", "pay_way", "list", test_pay_way_list),
    ("Tag List", "tag", "list", test_tag_list),
    ("UserAddress List", "user", "list", test_user_address_list),
]


def run_audit() -> AuditReport:
    """Run the complete audit."""
    results = []
    total_queries = 0
    total_duplicates = 0

    for name, category, endpoint_type, test_func in ALL_TESTS:
        result = run_test(name, category, endpoint_type, test_func)
        results.append(result)
        total_queries += result.query_count
        total_duplicates += result.duplicate_queries

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    # Generate summary by category
    summary = {}
    for result in results:
        if result.category not in summary:
            summary[result.category] = {
                "passed": 0,
                "failed": 0,
                "total_queries": 0,
            }
        if result.passed:
            summary[result.category]["passed"] += 1
        else:
            summary[result.category]["failed"] += 1
        summary[result.category]["total_queries"] += result.query_count

    return AuditReport(
        timestamp=datetime.now().isoformat(),
        total_endpoints=len(results),
        passed_endpoints=passed,
        failed_endpoints=failed,
        total_queries=total_queries,
        duplicate_queries=total_duplicates,
        results=results,
        summary=summary,
    )


def format_console(report: AuditReport) -> str:
    """Format report for console output."""
    lines = [
        "=" * 70,
        "API QUERY PERFORMANCE AUDIT REPORT",
        f"Generated: {report.timestamp}",
        "=" * 70,
        "",
        f"Total Endpoints: {report.total_endpoints}",
        f"Passed: {report.passed_endpoints}",
        f"Failed: {report.failed_endpoints}",
        f"Total Queries: {report.total_queries}",
        f"Duplicate Queries: {report.duplicate_queries}",
        "",
        "-" * 70,
        "RESULTS BY CATEGORY",
        "-" * 70,
    ]

    current_category = None
    for result in report.results:
        if result.category != current_category:
            current_category = result.category
            lines.append(f"\n{current_category.upper()}")
            lines.append("-" * 40)

        status = "[PASS]" if result.passed else "[FAIL]"
        lines.append(
            f"  {status} {result.name}: "
            f"{result.query_count}/{result.max_allowed} queries, "
            f"{result.execution_time_ms:.0f}ms"
        )

        if result.error:
            lines.append(f"         Error: {result.error}")

        if result.recommendations:
            for rec in result.recommendations:
                lines.append(f"         -> {rec}")

    # Summary by category
    lines.extend(
        [
            "",
            "-" * 70,
            "SUMMARY BY CATEGORY",
            "-" * 70,
        ]
    )

    for category, stats in report.summary.items():
        lines.append(
            f"  {category}: {stats['passed']}/{stats['passed'] + stats['failed']} passed, "
            f"{stats['total_queries']} total queries"
        )

    lines.extend(["", "=" * 70])

    return "\n".join(lines)


def format_json(report: AuditReport) -> str:
    """Format report as JSON."""

    def serialize(obj):
        if hasattr(obj, "__dict__"):
            return asdict(obj)
        return str(obj)

    return json.dumps(asdict(report), indent=2, default=serialize)


def format_markdown(report: AuditReport) -> str:
    """Format report as Markdown."""
    lines = [
        "# API Query Performance Audit Report",
        "",
        f"**Generated:** {report.timestamp}",
        "",
        "## Summary",
        "",
        f"- **Total Endpoints:** {report.total_endpoints}",
        f"- **Passed:** {report.passed_endpoints}",
        f"- **Failed:** {report.failed_endpoints}",
        f"- **Total Queries:** {report.total_queries}",
        f"- **Duplicate Queries:** {report.duplicate_queries}",
        "",
        "## Results by Category",
        "",
    ]

    current_category = None
    for result in report.results:
        if result.category != current_category:
            current_category = result.category
            lines.append(f"### {current_category.title()}")
            lines.append("")
            lines.append("| Endpoint | Status | Queries | Time |")
            lines.append("|----------|--------|---------|------|")

        status = "✅" if result.passed else "❌"
        lines.append(
            f"| {result.name} | {status} | "
            f"{result.query_count}/{result.max_allowed} | "
            f"{result.execution_time_ms:.0f}ms |"
        )

        if result.recommendations:
            lines.append("")
            lines.append(f"**Recommendations for {result.name}:**")
            for rec in result.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

    # Summary table
    lines.extend(
        [
            "",
            "## Summary by Category",
            "",
            "| Category | Passed | Failed | Total Queries |",
            "|----------|--------|--------|---------------|",
        ]
    )

    for category, stats in report.summary.items():
        total = stats["passed"] + stats["failed"]
        lines.append(
            f"| {category} | {stats['passed']}/{total} | "
            f"{stats['failed']} | {stats['total_queries']} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive API Query Performance Audit"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path (default: print to console)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["console", "json", "markdown"],
        default="console",
        help="Output format (default: console)",
    )

    args = parser.parse_args()

    print("Running API Query Performance Audit...")
    print("This may take a moment...\n")

    report = run_audit()

    # Format output
    if args.format == "json":
        output = format_json(report)
    elif args.format == "markdown":
        output = format_markdown(report)
    else:
        output = format_console(report)

    # Write or print output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Report saved to: {args.output}")
    else:
        print(output)

    # Exit with error code if any tests failed
    sys.exit(0 if report.failed_endpoints == 0 else 1)


if __name__ == "__main__":
    main()
