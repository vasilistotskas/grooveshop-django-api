from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.utils import timezone

from admin.dashboard import DASHBOARD_CACHE_KEY

_LAST_LOGIN_DISPATCH_UID = "admin.tenant_aware_update_last_login"


def _tenant_aware_update_last_login(sender, user, request=None, **kwargs):
    """``user_logged_in`` receiver — schema-aware replacement for Django's
    stock ``update_last_login``.

    ``update_last_login`` saves the ``User`` instance it's handed — for
    a platform-staff session (``PlatformStaffBackend``) that instance
    is the PUBLIC-schema row, but the save would otherwise run under
    whatever schema the request's HOST resolved to (a tenant schema on
    any tenant host), producing the well-known "UPDATE affected 0 rows"
    crash or, worse, silently touching an unrelated same-pk row there.
    Every other login path (allauth, plain ModelBackend on the public
    host) keeps Django's stock behaviour.
    """
    from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

    if getattr(user, "backend", "") == PLATFORM_STAFF_BACKEND_PATH:
        from django_tenants.utils import get_public_schema_name, schema_context

        with schema_context(get_public_schema_name()):
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])
        return

    from django.contrib.auth.models import update_last_login

    update_last_login(sender, user, request=request, **kwargs)


def _connect_tenant_aware_last_login():
    """Disconnect Django's stock ``update_last_login`` and connect ours.

    Must run AFTER ``django.contrib.auth``'s ``AuthConfig.ready()`` has
    connected the stock receiver — ``admin`` appears later than
    ``django.contrib.auth`` in ``SHARED_APPS``, so ``AppConfig.ready()``
    ordering guarantees that here.
    """
    from django.contrib.auth.signals import user_logged_in

    user_logged_in.disconnect(dispatch_uid="update_last_login")
    user_logged_in.connect(
        _tenant_aware_update_last_login,
        dispatch_uid=_LAST_LOGIN_DISPATCH_UID,
        weak=False,
    )


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
