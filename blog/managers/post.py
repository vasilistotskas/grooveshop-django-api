from django.db import models
from django.db.models import Count
from parler.managers import TranslatableManager, TranslatableQuerySet


class BlogPostQuerySet(TranslatableQuerySet):
    def with_likes_count(self):
        return self.annotate(likes_count_field=Count("likes", distinct=True))

    def with_comments_count(self):
        return self.annotate(
            comments_count_field=Count("comments", distinct=True)
        )

    def with_tags_count(self):
        return self.annotate(
            tags_count_field=Count(
                "tags", distinct=True, filter=models.Q(tags__active=True)
            )
        )

    def with_all_annotations(self):
        return self.with_likes_count().with_comments_count().with_tags_count()


class BlogPostManager(TranslatableManager):
    def get_queryset(self) -> BlogPostQuerySet:
        return BlogPostQuerySet(self.model, using=self._db)

    def with_likes_count(self):
        return self.get_queryset().with_likes_count()

    def with_comments_count(self):
        return self.get_queryset().with_comments_count()

    def with_tags_count(self):
        return self.get_queryset().with_tags_count()

    def with_all_annotations(self):
        return self.get_queryset().with_all_annotations()
