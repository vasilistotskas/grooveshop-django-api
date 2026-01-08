from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.db.models import Count, Q
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class BlogPostQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for BlogPost model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

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
        return self.annotate(
            _likes_count=Count("likes", distinct=True),
            _comments_count=Count(
                "comments",
                distinct=True,
                filter=Q(comments__approved=True),
            ),
            _tags_count=Count(
                "tags",
                distinct=True,
                filter=Q(tags__active=True),
            ),
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

    # Legacy methods for backward compatibility
    def with_likes_count_annotation(self) -> Self:
        return self.annotate(
            likes_count_annotation=Count("likes", distinct=True)
        )

    def with_comments_count_annotation(self) -> Self:
        return self.annotate(
            comments_count_annotation=Count(
                "comments",
                distinct=True,
                filter=models.Q(comments__approved=True),
            )
        )

    def with_tags_count_annotation(self) -> Self:
        return self.annotate(
            tags_count_annotation=Count(
                "tags", distinct=True, filter=models.Q(tags__active=True)
            )
        )


class BlogPostManager(TranslatableManager):
    """
    Manager for BlogPost model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogPost.objects.for_list()
            return BlogPost.objects.for_detail()
    """

    def get_queryset(self) -> BlogPostQuerySet:
        return BlogPostQuerySet(self.model, using=self._db)

    def for_list(self) -> BlogPostQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> BlogPostQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    # Legacy methods for backward compatibility
    def with_likes_count_annotation(self) -> BlogPostQuerySet:
        return self.get_queryset().with_likes_count_annotation()

    def with_comments_count_annotation(self) -> BlogPostQuerySet:
        return self.get_queryset().with_comments_count_annotation()

    def with_tags_count_annotation(self) -> BlogPostQuerySet:
        return self.get_queryset().with_tags_count_annotation()
