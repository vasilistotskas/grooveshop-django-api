from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, Exists, OuterRef, Q
from mptt.managers import TreeManager
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class BlogCategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    """
    Optimized QuerySet for BlogCategory model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    @classmethod
    def as_manager(cls):
        manager = BlogCategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True  # type: ignore[attr-defined]

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_parent(self) -> Self:
        """Select related parent category with translations."""
        return self.select_related("parent").prefetch_related(
            "parent__translations"
        )

    def with_children(self) -> Self:
        """Prefetch children categories."""
        return self.prefetch_related("children", "children__translations")

    def with_posts(self) -> Self:
        """Prefetch blog posts."""
        return self.prefetch_related("blog_posts")

    def with_counts(self) -> Self:
        """
        Annotate with post_count and has_children to avoid N+1 queries.

        These annotations are used by BlogCategorySerializer.
        """
        from blog.models.category import BlogCategory  # noqa: PLC0415

        children_subquery = BlogCategory.objects.filter(
            parent=OuterRef("pk")
        ).values("pk")[:1]

        return self.annotate(
            _post_count=Count(
                "blog_posts", filter=Q(blog_posts__is_published=True)
            ),
            _has_children=Exists(children_subquery),
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes parent, translations, and count annotations.
        """
        return self.with_parent().with_translations().with_counts()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus children and posts.
        """
        return self.for_list().with_children().with_posts()

    def for_tree(self) -> Self:
        """
        Optimized queryset for tree views.

        Includes parent, translations, children, posts, and counts.
        """
        return (
            self.with_parent()
            .with_translations()
            .with_children()
            .with_posts()
            .with_counts()
        )


class BlogCategoryManager(TreeManager, TranslatableManager):
    """
    Manager for BlogCategory model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogCategory.objects.for_list()
            return BlogCategory.objects.for_detail()
    """

    _queryset_class = BlogCategoryQuerySet

    def for_list(self) -> BlogCategoryQuerySet:
        """Return optimized queryset for list views."""
        return self._queryset_class(self.model, using=self._db).for_list()

    def for_detail(self) -> BlogCategoryQuerySet:
        """Return optimized queryset for detail views."""
        return self._queryset_class(self.model, using=self._db).for_detail()

    def for_tree(self) -> BlogCategoryQuerySet:
        """Return optimized queryset for tree views."""
        return self._queryset_class(self.model, using=self._db).for_tree()
