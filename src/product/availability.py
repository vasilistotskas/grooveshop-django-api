"""Whether a save moved a product between available and sold out.

Every sale saves ``stock`` alone (``order/stock.py``), so a save of ``stock``
and ``updated_at`` is bookkeeping: the cached pages and the page builder's
lists are not purged for it. The one such save that changes what they show is
the one that sells a product out or brings it back, because the product list
puts products in stock first (``availability_priority`` in
``product.views.product``) and a builder list can leave sold-out ones out.

The stored stock is read by a ``pre_save`` that also reads the stored
category, in ONE query, so a sale costs no query for either consumer:
``core.cache.invalidation`` (through :func:`availability_flipped`, registered
as a significant save of ``product.Product``) and ``builder.data_purge``.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models.signals import pre_save

from core.cache.invalidation import register_significant_save

_UID = "product:availability:remember-stored"


def _remember_stored(sender, instance, **kwargs) -> None:
    """Keep the stored category and stock on the instance, so the receivers
    after the save know where the product WAS."""
    if kwargs.get("raw") or instance.pk is None:
        return
    row = (
        sender._base_manager.filter(pk=instance.pk)
        .values_list("category_id", "stock")
        .first()
    )
    instance._stored_category_id, instance._stored_stock = row or (None, None)


def availability_flipped(instance) -> bool:
    """Whether the save moved the product between available (stock above
    zero, the product list's own test) and sold out. Without the stored stock
    nothing is known and nothing flips."""
    before = getattr(instance, "_stored_stock", None)
    return before is not None and (before > 0) != (instance.stock > 0)


def stored_category_id(instance) -> int | None:
    """The category the product had before this save."""
    return getattr(instance, "_stored_category_id", None)


register_significant_save("product.Product", availability_flipped)


def connect(*, force: bool = False) -> None:
    """Wire the ``pre_save``. Skipped when caching is disabled (nothing is
    cached, so nothing reads it); ``force`` is for the tests."""
    if getattr(settings, "DISABLE_CACHE", False) and not force:
        return
    from product.models.product import Product

    pre_save.connect(
        _remember_stored, sender=Product, dispatch_uid=_UID, weak=False
    )


def disconnect() -> None:
    """Undo :func:`connect`, for the tests that wire it with ``force``."""
    from product.models.product import Product

    pre_save.disconnect(sender=Product, dispatch_uid=_UID)
