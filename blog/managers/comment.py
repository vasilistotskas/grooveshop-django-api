from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet


class BlogCommentQuerySet(TranslatableQuerySet, TreeQuerySet):
    @classmethod
    def as_manager(cls):
        manager = BlogCommentManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]

    def approved(self):
        return self.filter(is_approved=True)


class BlogCommentManager(TreeManager, TranslatableManager):
    _queryset_class = BlogCommentQuerySet

    def approved(self):
        return BlogCommentQuerySet(self.model, using=self._db).approved()
