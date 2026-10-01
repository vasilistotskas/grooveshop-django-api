"""Sidebar badge callables (``UNFOLD["SIDEBAR"]`` ``badge`` entries).

Every count comes from ONE cached query,
``admin.dashboard.store.queries.sidebar_counts``, cleared by writes to
the models behind it; a render calls up to nine of these, so the result
is also memoised on the request. ``None`` for zero: Unfold's badge
template shows nothing for it.
"""

from __future__ import annotations

from typing import Any

from admin.dashboard.store.queries import sidebar_counts


def _count(request: Any, key: str) -> int | None:
    counts = getattr(request, "_admin_sidebar_counts", None)
    if counts is None:
        counts = sidebar_counts.get()
        request._admin_sidebar_counts = counts
    return counts[key] or None


def pending_orders_badge(request):
    return _count(request, "pending_orders")


def pending_reviews_badge(request):
    return _count(request, "pending_reviews")


def pending_comments_badge(request):
    return _count(request, "pending_comments")


def new_messages_badge(request):
    """Contact messages received in the last 7 days."""
    return _count(request, "new_contact_messages")


def low_stock_badge(request):
    """Active products with ``0 < stock < 10``: replenish soon."""
    return _count(request, "low_stock")


def abandoned_carts_badge(request):
    """Carts idle between 24 hours and 30 days: the recovery queue."""
    return _count(request, "abandoned_carts")


def pending_business_profiles_badge(request):
    """B2B applications awaiting review."""
    return _count(request, "pending_business_profiles")


def draft_blog_posts_badge(request):
    """Unpublished blog posts."""
    return _count(request, "draft_blog_posts")


def live_promotions_badge(request):
    """Promotions active and inside their schedule window."""
    return _count(request, "live_promotions")
