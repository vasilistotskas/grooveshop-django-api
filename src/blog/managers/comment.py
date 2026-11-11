from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import OuterRef

from core.db.aggregates import subquery_count
from core.managers import TreeTranslatableManager, TreeTranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class BlogCommentQuerySet(TreeTranslatableQuerySet):
    """
    Optimized QuerySet for BlogComment model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

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

    def with_engagement(self) -> Self:
        """Annotate ``likes_count`` and ``replies_count`` (approved
        replies) — read by ``BlogComment``'s properties of those names
        and by ``?ordering=``."""
        from blog.models.comment import BlogComment

        return self.annotate(
            likes_count=subquery_count(
                BlogComment.likes.through.objects.filter(
                    blogcomment=OuterRef("pk")
                ),
                "blogcomment",
            ),
            replies_count=subquery_count(
                BlogComment.objects.filter(
                    parent=OuterRef("pk"), approved=True
                ),
                "parent",
            ),
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, post, parent, translations, children, likes and
        the engagement counts.
        """
        return (
            self.with_user()
            .with_post()
            .with_parent()
            .with_translations()
            .with_children()
            .with_likes()
            .with_post_translations()
            .with_engagement()
        )

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this model.
        """
        return self.for_list()

    def approved(self):
        return self.filter(approved=True)


class BlogCommentManager(TreeTranslatableManager):
    """
    Manager for BlogComment model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogComment.objects.for_list()
            return BlogComment.objects.for_detail()
    """

    queryset_class = BlogCommentQuerySet
