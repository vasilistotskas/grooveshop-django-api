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
