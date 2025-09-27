from django.db import models
from django.db.models import Count
from parler.managers import TranslatableManager, TranslatableQuerySet


class BlogPostQuerySet(TranslatableQuerySet):
    def with_likes_count_annotation(self):
        return self.annotate(
            likes_count_annotation=Count("likes", distinct=True)
        )

    def with_comments_count_annotation(self):
        return self.annotate(
            comments_count_annotation=Count(
                "comments",
                distinct=True,
                filter=models.Q(comments__approved=True),
            )
        )

    def with_tags_count_annotation(self):
        return self.annotate(
            tags_count_annotation=Count(
                "tags", distinct=True, filter=models.Q(tags__active=True)
            )
        )


class BlogPostManager(TranslatableManager):
    def get_queryset(self) -> BlogPostQuerySet:
        return BlogPostQuerySet(self.model, using=self._db)

    def with_likes_count_annotation(self):
        return self.get_queryset().with_likes_count_annotation()

    def with_comments_count_annotation(self):
        return self.get_queryset().with_comments_count_annotation()

    def with_tags_count_annotation(self):
        return self.get_queryset().with_tags_count_annotation()
