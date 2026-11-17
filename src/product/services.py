"""Catalogue writes that more than one surface performs.

The admin's bulk and row actions on products call these; the rules live
here so a price field is changed the same way wherever it is changed.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.db.models import QuerySet

from product.models.product import Product

#: How many ``-copy-<n>`` suffixes a duplicate tries before it gives up.
MAX_COPY_SUFFIX = 100


class ProductDuplicationError(Exception):
    """No free slug or SKU was found for the copy of a product."""


class ProductService:
    @staticmethod
    def set_discount(queryset: QuerySet[Product], percent: Decimal) -> int:
        """Set ``discount_percent`` on every product of *queryset*.

        Saved one at a time, never ``queryset.update()``. A bulk UPDATE
        emits no ``post_save``, so simple-history writes no row,
        ``post_create_historical_record_callback`` never runs,
        ``product_price_lowered`` is never sent, and not one price-drop
        alert reaches the customers who subscribed to it. ``final_price``
        and ``discount_percent`` are also indexed Meilisearch fields, so
        search kept the pre-discount price until an unrelated save. The cost
        is bounded by the operator's selection.

        Rows are locked in pk order, on the product table alone: a selection
        of many products and a checkout over some of them share rows, and
        two transactions that take shared rows in different orders deadlock
        (see ``StockManager.lock_products``). Returns the number saved.
        """
        updated = 0
        with transaction.atomic():
            for product in queryset.order_by("pk").select_for_update(
                of=("self",)
            ):
                product.discount_percent = percent
                product.save(update_fields=["discount_percent"])
                updated += 1
        return updated

    @classmethod
    def clear_discount(cls, queryset: QuerySet[Product]) -> int:
        """Remove the discount of every product of *queryset*.

        Saved individually for the same reason as :meth:`set_discount`.
        Clearing a discount RAISES the price, so no alert is due, but
        ``final_price`` is an indexed Meilisearch field and search would
        otherwise keep advertising the discounted one.
        """
        return cls.set_discount(queryset, Decimal("0.0"))

    @staticmethod
    def duplicate_as_draft(source: Product) -> Product:
        """Clone *source* into an inactive draft and return it.

        Translations, images, attributes and stock are deliberately NOT
        copied: the catalog team uses the duplicate as a starting skeleton.
        ``slug`` and ``sku`` get a ``-copy-<n>`` suffix so the unique
        constraints hold; ``active=False`` keeps the draft out of the
        storefront until edits are finished.

        Raises :class:`ProductDuplicationError` when no suffix up to
        ``MAX_COPY_SUFFIX`` leaves both free.
        """
        clone = Product.objects.get(pk=source.pk)
        clone.pk = None
        clone.id = None
        # ``uuid`` is ``UUIDField(default=uuid4, unique=True)`` with no
        # ``null=True``: an explicit None OVERRIDES the default and the
        # INSERT sent NULL. With ``pk`` cleared the row is an insert, and
        # ``uuid`` still holds the original's value, so it is reset to a
        # fresh one.
        clone.uuid = uuid4()
        # Unique per product: the copy is not the other system's product.
        clone.external_reference = None
        clone.active = False
        clone.stock = 0
        clone.view_count = 0
        clone.low_stock_alert_sent = False

        # Append a numeric suffix to slug/sku until both are unique.
        n = 1
        base_slug = source.slug
        base_sku = getattr(source, "sku", "") or ""
        while True:
            candidate_slug = f"{base_slug}-copy-{n}"
            candidate_sku = f"{base_sku}-COPY-{n}" if base_sku else ""
            slug_taken = Product.objects.filter(slug=candidate_slug).exists()
            sku_taken = (
                bool(candidate_sku)
                and Product.objects.filter(sku=candidate_sku).exists()
            )
            if not slug_taken and not sku_taken:
                clone.slug = candidate_slug
                if candidate_sku:
                    clone.sku = candidate_sku
                break
            n += 1
            if n > MAX_COPY_SUFFIX:
                raise ProductDuplicationError(source.pk)

        clone.save()
        return clone
