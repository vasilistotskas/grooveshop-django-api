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

Async Tasks:
    from meili.tasks import reindex_model_task

    # Reindex all products asynchronously
    reindex_model_task.delay("product", "product")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meili.models import IndexMixin
    from meili.querysets import IndexQuerySet

# Lazy imports to avoid AppRegistryNotReady errors during Django startup
_LAZY_IMPORTS = {
    "IndexMixin": "meili.models",
    "IndexQuerySet": "meili.querysets",
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
    "IndexMixin",
    "IndexQuerySet",
]
