from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, Q

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class BlogPostQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for BlogPost model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_likes_count(self) -> Self:
        """Annotate with likes count."""
        return self.annotate(likes_count=Count("likes", distinct=True))

    def with_comments_count(
        self,
        approved_only: bool = True,
    ) -> Self:
        """Annotate queryset with comments count."""
        if approved_only:
            return self.annotate(
                comments_count=Count(
                    "comments",
                    distinct=True,
                    filter=Q(comments__approved=True),
                )
            )
        return self.annotate(comments_count=Count("comments", distinct=True))

    def with_tags_count(
        self,
        active_only: bool = True,
    ) -> Self:
        """Annotate queryset with tags count."""
        if active_only:
            return self.annotate(
                tags_count=Count(
                    "tags",
                    distinct=True,
                    filter=Q(tags__active=True),
                )
            )
        return self.annotate(tags_count=Count("tags", distinct=True))

    def with_author(self) -> Self:
        """Select related author and user."""
        return self.select_related("author__user", "author")

    def with_category(self) -> Self:
        """Select related category with translations."""
        return self.select_related("category").prefetch_related(
            "category__translations"
        )

    def with_tags(self) -> Self:
        """Prefetch tags with translations."""
        return self.prefetch_related("tags__translations")

    def with_likes(self) -> Self:
        """Prefetch likes for efficient access."""
        return self.prefetch_related("likes")

    def with_comments(self) -> Self:
        """Prefetch comments for efficient access."""
        return self.prefetch_related("comments")

    def with_counts(self) -> Self:
        """Annotate with all count fields for efficient property access."""
        return (
            self.with_likes_count()
            .with_comments_count(approved_only=True)
            .with_tags_count(active_only=True)
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes author, category, translations, and counts.
        """
        return (
            self.with_author()
            .with_category()
            .with_translations()
            .with_tags()
            .with_likes()
            .with_comments()
            .with_counts()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() since blog posts need all data.
        """
        return self.for_list()


class BlogPostManager(TranslatableOptimizedManager):
    """
    Manager for BlogPost model with optimized queryset methods.

    Most methods are automatically delegated to BlogPostQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogPost.objects.for_list()
            return BlogPost.objects.for_detail()
    """

    queryset_class = BlogPostQuerySet

    def get_queryset(self) -> BlogPostQuerySet:
        return BlogPostQuerySet(self.model, using=self._db)

    def for_list(self) -> BlogPostQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> BlogPostQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
