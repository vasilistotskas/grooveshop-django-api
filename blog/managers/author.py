from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone

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

    def with_posts_prefetch(self) -> Self:
        """Prefetch blog posts."""
        return self.prefetch_related("blog_posts")

    def with_posts_details(self) -> Self:
        """Prefetch blog posts with full details."""
        return self.prefetch_related(
            "blog_posts",
            "blog_posts__likes",
            "blog_posts__category",
            "blog_posts__translations",
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user and translations.
        """
        return self.with_user().with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus posts details.
        """
        return self.for_list().with_posts_details()

    def with_posts(self):
        """Filter authors who have at least one blog post."""
        return self.filter(blog_posts__isnull=False).distinct()

    def without_posts(self):
        return self.filter(blog_posts__isnull=True)

    def active(self):
        cutoff_date = timezone.now() - timedelta(days=180)
        return self.filter(blog_posts__created_at__gte=cutoff_date).distinct()

    def with_website(self):
        return self.exclude(Q(website="") | Q(website__isnull=True))

    def with_bio(self):
        return self.exclude(
            Q(translations__bio__isnull=True) | Q(translations__bio__exact="")
        ).distinct()


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
