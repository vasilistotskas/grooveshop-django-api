from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps
from django.db.models import (
    F,
    Func,
    IntegerField,
    Model,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
)
from django.db.models.functions import Coalesce

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from tenant.membership import is_store_staff

if TYPE_CHECKING:
    from typing import Self


def _count(rows: QuerySet) -> Coalesce:
    """The number of ``rows``, as a correlated subquery.

    A subquery rather than ``Count(..., distinct=True)``: three joined
    aggregates (likes, comments, tags) multiply into likes x comments x
    tags rows per post before they are grouped, which a popular post
    turns into millions. Each subquery reads only its own table.
    """
    return Coalesce(
        Subquery(
            rows.order_by()
            .annotate(n=Func(F("pk"), function="COUNT"))
            .values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


def _through_rows(model: type[Model], field: str, **extra) -> QuerySet:
    """The rows joining each ``model`` to its ``field`` relation."""
    relation = model._meta.get_field(field)
    return relation.remote_field.through._default_manager.filter(
        **{relation.m2m_field_name(): OuterRef("pk")}, **extra
    )


class BlogPostQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for BlogPost model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_likes_count(self) -> Self:
        """Annotate with likes count."""
        return self.annotate(
            likes_count=_count(_through_rows(self.model, "likes"))
        )

    def with_comments_count(
        self,
        approved_only: bool = True,
    ) -> Self:
        """Annotate queryset with comments count."""
        comments = apps.get_model("blog", "BlogComment")._base_manager
        rows = comments.filter(post=OuterRef("pk"))
        if approved_only:
            rows = rows.filter(approved=True)
        return self.annotate(comments_count=_count(rows))

    def with_tags_count(
        self,
        active_only: bool = True,
    ) -> Self:
        """Annotate queryset with tags count."""
        relation = self.model._meta.get_field("tags")
        extra = (
            {f"{relation.m2m_reverse_field_name()}__active": True}
            if active_only
            else {}
        )
        return self.annotate(
            tags_count=_count(_through_rows(self.model, "tags", **extra))
        )

    def published(self) -> Self:
        """Only posts visible to the public.

        A post is public when ``is_published`` is set and its
        ``published_at`` is unset or already in the past. Mirrors
        ``core.models.PublishedQuerySet.published``.
        """
        from django.utils import timezone

        now = timezone.now()
        return self.filter(
            Q(published_at__lte=now, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )

    def visible_to(self, user) -> Self:
        """Public visibility gate for the read-only blog endpoints.

        Staff see drafts (admin preview); everyone else sees only
        ``published()``. Every public entry point into blog posts must
        route through this. The reverse FK accessors
        (``category.blog_posts`` / ``author.blog_posts``) bypass this
        manager entirely: listed raw, they exposed drafts to anonymous
        callers and rendered category cards linking to a detail route
        that then 404'd.
        """
        if is_store_staff(user):
            return self
        return self.published()

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
            .with_comments()
            .with_counts()
        )

    def for_feed(self) -> Self:
        """The posts with their translations and nothing else.

        The feed renders each post's own columns and translations. The
        likes, comments and tags ``for_list`` joins to count them would
        multiply into millions of rows for a popular post, and nothing
        reads them here.
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        for_list() plus the likers: only the detail serializer lists them.
        """
        return self.for_list().with_likes()


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

    def for_feed(self) -> BlogPostQuerySet:
        """Return the posts with their translations only, for the feed."""
        return self.get_queryset().for_feed()

    def for_detail(self) -> BlogPostQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
