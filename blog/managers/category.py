from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet


class BlogCategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    @classmethod
    def as_manager(cls):
        manager = BlogCategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]


class BlogCategoryManager(TreeManager, TranslatableManager):
    _queryset_class = BlogCategoryQuerySet
