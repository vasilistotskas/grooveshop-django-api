from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from admin.dashboard import DASHBOARD_CACHE_KEY


def _invalidate_dashboard_cache(*args, **kwargs):
    cache.delete(DASHBOARD_CACHE_KEY)


def _connect_dashboard_invalidation():
    """Bust the admin dashboard cache on writes to domain models it reads.

    Kept as a function so the import of model classes is deferred until
    apps are loaded (avoids circular imports at module import time).
    """
    from blog.models.comment import BlogComment
    from blog.models.post import BlogPost
    from cart.models import Cart
    from contact.models import Contact
    from order.models.order import Order
    from order.models.stock_log import StockLog
    from product.models.product import Product
    from product.models.review import ProductReview
    from user.models.account import UserAccount
    from user.models.subscription import UserSubscription

    for model in (
        Order,
        Product,
        ProductReview,
        BlogPost,
        BlogComment,
        UserAccount,
        UserSubscription,
        Cart,
        Contact,
        StockLog,
    ):
        sender_uid = f"admin.dashboard_invalidate:{model._meta.label}"
        post_save.connect(
            _invalidate_dashboard_cache,
            sender=model,
            dispatch_uid=f"{sender_uid}:save",
            weak=False,
        )
        post_delete.connect(
            _invalidate_dashboard_cache,
            sender=model,
            dispatch_uid=f"{sender_uid}:delete",
            weak=False,
        )
