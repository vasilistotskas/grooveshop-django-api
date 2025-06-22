from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet


class BlogAuthorQuerySet(TranslatableQuerySet):
    def with_posts(self):
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

    def with_user_details(self):
        return self.select_related("user").prefetch_related(
            "blog_posts",
            "blog_posts__likes",
            "blog_posts__category",
            "translations",
        )


class BlogAuthorManager(TranslatableManager):
    def get_queryset(self):
        return BlogAuthorQuerySet(self.model, using=self._db)

    def with_posts(self):
        return self.get_queryset().with_posts()

    def without_posts(self):
        return self.get_queryset().without_posts()

    def active(self):
        return self.get_queryset().active()

    def with_website(self):
        return self.get_queryset().with_website()

    def with_bio(self):
        return self.get_queryset().with_bio()

    def with_user_details(self):
        return self.get_queryset().with_user_details()
