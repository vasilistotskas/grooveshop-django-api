"""
Meilisearch integration for Django.

This package provides seamless integration between Django models and Meilisearch,
enabling full-text search with automatic indexing via signals.

Quick Start:
    1. Add 'meili' to INSTALLED_APPS
    2. Configure MEILISEARCH settings
    3. Inherit from IndexMixin in your models
    4. Define MeiliMeta class with index configuration

Example:
    from meili import IndexMixin

    class Product(IndexMixin):
        name = models.CharField(max_length=255)
        price = models.DecimalField(...)

        class MeiliMeta:
            filterable_fields = ("name", "price", "category")
            searchable_fields = ("name", "description")
            sortable_fields = ("price", "created_at")

    # Search products
    results = Product.meilisearch.filter(price__gte=100).search("laptop")

Batch Operations:
    from meili.batch import BatchIndexer, suspend_indexing

    # Batch indexing
    with BatchIndexer() as indexer:
        for product in products:
            indexer.add(product)

    # Suspend automatic indexing
    with suspend_indexing():
        Product.objects.bulk_create(products)

Async Tasks:
    from meili.tasks import reindex_model_task

    # Reindex all products asynchronously
    reindex_model_task.delay("product", "product")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meili.batch import (
        BatchIndexer,
        batch_index_context,
        bulk_index_queryset,
        suspend_indexing,
    )
    from meili.models import IndexMixin, MeiliGeo
    from meili.querysets import BoundingBox, IndexQuerySet, Point, Radius

# Lazy imports to avoid AppRegistryNotReady errors during Django startup
_LAZY_IMPORTS = {
    "IndexMixin": "meili.models",
    "MeiliGeo": "meili.models",
    "IndexQuerySet": "meili.querysets",
    "Radius": "meili.querysets",
    "BoundingBox": "meili.querysets",
    "Point": "meili.querysets",
    "BatchIndexer": "meili.batch",
    "batch_index_context": "meili.batch",
    "bulk_index_queryset": "meili.batch",
    "suspend_indexing": "meili.batch",
}


def __getattr__(name: str):
    """Lazy import attributes to avoid Django app registry issues."""
    if name in _LAZY_IMPORTS:
        module_path = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Return available attributes for autocomplete."""
    return list(_LAZY_IMPORTS.keys())


__all__ = [
    # Core
    "IndexMixin",
    "IndexQuerySet",
    "MeiliGeo",
    # Geo types
    "Radius",
    "BoundingBox",
    "Point",
    # Batch utilities
    "BatchIndexer",
    "batch_index_context",
    "bulk_index_queryset",
    "suspend_indexing",
]
