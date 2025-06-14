from __future__ import annotations

from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet


class CategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    @classmethod
    def as_manager(cls):
        manager = CategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]

    def active(self):
        return self.filter(active=True)


class CategoryManager(TreeManager, TranslatableManager):
    _queryset_class = CategoryQuerySet

    def active(self):
        return self.get_queryset().active()
