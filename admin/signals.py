from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from admin.dashboard import DASHBOARD_CACHE_KEY


def _invalidate_dashboard_cache(*args, **kwargs):
    cache.delete(DASHBOARD_CACHE_KEY)


def _connect_dashboard_invalidation():
    """Bust the admin dashboard cache on writes to domain models it reads.

    The new (Stage 2) dashboard surfaces revenue, orders, customers,
    pending reviews, and contact messages — anything that affects those
    numbers must invalidate the cache. Stock writes invalidate too via
    Order/Product saves; we don't subscribe to ``StockLog`` directly
    because the data feeds Zone D (low stock), which is already
    computed fresh per request.
    """

    from contact.models import Contact
    from order.models.invoice import Invoice
    from order.models.order import Order
    from product.models.product import Product
    from product.models.review import ProductReview
    from user.models.account import UserAccount

    for model in (Order, Invoice, Product, ProductReview, UserAccount, Contact):
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
