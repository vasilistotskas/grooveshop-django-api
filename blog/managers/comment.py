from __future__ import annotations

from typing import TYPE_CHECKING

from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class BlogCommentQuerySet(TranslatableQuerySet, TreeQuerySet):
    """
    Optimized QuerySet for BlogComment model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    @classmethod
    def as_manager(cls):
        manager = BlogCommentManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def with_post(self) -> Self:
        """Select related post with category and author."""
        return self.select_related("post", "post__category", "post__author")

    def with_post_translations(self) -> Self:
        """Prefetch post translations."""
        return self.prefetch_related("post__translations")

    def with_parent(self) -> Self:
        """Select related parent comment."""
        return self.select_related("parent")

    def with_children(self) -> Self:
        """Prefetch children comments."""
        return self.prefetch_related("children")

    def with_likes(self) -> Self:
        """Prefetch likes."""
        return self.prefetch_related("likes")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, post, parent, translations, children, and likes.
        """
        return (
            self.with_user()
            .with_post()
            .with_parent()
            .with_translations()
            .with_children()
            .with_likes()
            .with_post_translations()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this model.
        """
        return self.for_list()

    def approved(self):
        return self.filter(approved=True)


class BlogCommentManager(TreeManager, TranslatableManager):
    """
    Manager for BlogComment model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogComment.objects.for_list()
            return BlogComment.objects.for_detail()
    """

    _queryset_class = BlogCommentQuerySet

    def for_list(self) -> BlogCommentQuerySet:
        """Return optimized queryset for list views."""
        return self._queryset_class(self.model, using=self._db).for_list()

    def for_detail(self) -> BlogCommentQuerySet:
        """Return optimized queryset for detail views."""
        return self._queryset_class(self.model, using=self._db).for_detail()

    def approved(self):
        return self._queryset_class(self.model, using=self._db).approved()
