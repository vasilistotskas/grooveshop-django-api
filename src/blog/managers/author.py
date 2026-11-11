from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import OuterRef

from core.db.aggregates import subquery_count
from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class BlogAuthorQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for BlogAuthor model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def with_posts_details(self) -> Self:
        """Prefetch blog posts with full details."""
        return self.prefetch_related(
            "blog_posts",
            "blog_posts__likes",
            "blog_posts__category",
            "blog_posts__translations",
        )

    def with_engagement(self) -> Self:
        """Annotate ``number_of_posts`` and ``total_likes_received``.

        Both count PUBLISHED posts only — the figures the author
        serializers show — and are what the filters and ``?ordering=``
        read, so a list never ranks or filters by a number it does not
        display.
        """
        from blog.models.post import BlogPost

        published = BlogPost.objects.published()
        return self.annotate(
            number_of_posts=subquery_count(
                published.filter(author=OuterRef("pk")), "author"
            ),
            total_likes_received=subquery_count(
                BlogPost.likes.through.objects.filter(
                    blogpost__in=published, blogpost__author=OuterRef("pk")
                ),
                "blogpost__author",
            ),
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, translations and the engagement counts.
        """
        return self.with_user().with_translations().with_engagement()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus posts details.
        """
        return self.for_list().with_posts_details()


class BlogAuthorManager(TranslatableOptimizedManager):
    """
    Manager for BlogAuthor model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogAuthor.objects.for_list()
            return BlogAuthor.objects.for_detail()
    """

    queryset_class = BlogAuthorQuerySet
