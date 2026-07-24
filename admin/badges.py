from django.apps import apps
from django.core.cache import cache

_BADGE_TTL = 60


def _cached_count(key: str, label: str, model: str, **filters) -> int | None:
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    try:
        model_cls = apps.get_model(label, model)
    except LookupError:
        return None
    value = model_cls.objects.filter(**filters).count()
    cache.set(key, value, _BADGE_TTL)
    return value or None


def pending_orders_badge(request):
    from order.enum.status import OrderStatus

    return _cached_count(
        "admin:badge:pending_orders",
        "order",
        "Order",
        status=OrderStatus.PENDING,
    )


def pending_reviews_badge(request):
    from product.enum.review import ReviewStatus

    return _cached_count(
        "admin:badge:pending_reviews",
        "product",
        "ProductReview",
        status=ReviewStatus.NEW,
    )


def pending_comments_badge(request):
    return _cached_count(
        "admin:badge:pending_comments",
        "blog",
        "BlogComment",
        approved=False,
    )


def unread_messages_badge(request):
    return _cached_count(
        "admin:badge:unread_messages",
        "contact",
        "Contact",
    )


def low_stock_badge(request):
    """Active products with `0 < stock < 10` — the "replenish soon" band.

    Out-of-stock items (`stock=0`) are intentionally excluded because
    that's a separate operational concern (deactivate or restock); we
    only badge the warning band so staff can act before things sell out.
    """

    return _cached_count(
        "admin:badge:low_stock",
        "product",
        "Product",
        active=True,
        stock__gt=0,
        stock__lt=10,
    )


def abandoned_carts_badge(request):
    """Carts inactive 24h-30d (older = stale, ignored).

    Surfaces the recovery queue right in the sidebar so the marketing
    team can see at a glance how many follow-up emails are pending.
    """

    from datetime import timedelta

    from django.utils import timezone

    cached = cache.get("admin:badge:abandoned_carts")
    if cached is not None:
        return cached or None
    try:
        Cart = apps.get_model("cart", "Cart")
    except LookupError:
        return None
    now = timezone.now()
    value = Cart.objects.filter(
        updated_at__lt=now - timedelta(hours=24),
        updated_at__gte=now - timedelta(days=30),
    ).count()
    cache.set("admin:badge:abandoned_carts", value, _BADGE_TTL)
    return value or None


def draft_blog_posts_badge(request):
    """Editorial queue depth — unpublished blog posts."""

    return _cached_count(
        "admin:badge:draft_blog_posts",
        "blog",
        "BlogPost",
        is_published=False,
    )
