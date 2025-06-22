from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet

from product.enum.review import ReviewStatus


class EnhancedReviewQuerySet(TranslatableQuerySet):
    def approved(self):
        return self.filter(status=ReviewStatus.TRUE)

    def pending(self):
        return self.filter(status=ReviewStatus.NEW)

    def rejected(self):
        return self.filter(status=ReviewStatus.FALSE)

    def published(self):
        return self.filter(is_published=True)

    def visible(self):
        return self.filter(is_published=True)

    def for_product(self, product):
        if hasattr(product, "pk"):
            return self.filter(product=product.pk)
        return self.filter(product=product)

    def for_user(self, user):
        if hasattr(user, "pk"):
            return self.filter(user=user.pk)
        return self.filter(user=user)

    def by_rate(self, rate):
        return self.filter(rate=rate)

    def high_rated(self, min_rate=4):
        return self.filter(rate__gte=min_rate)

    def low_rated(self, max_rate=2):
        return self.filter(rate__lte=max_rate)

    def recent(self, days=30):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def with_comments(self):
        return self.filter(translations__comment__isnull=False).distinct()

    def without_comments(self):
        return self.filter(
            Q(translations__comment__isnull=True)
            | Q(translations__comment__exact="")
        ).distinct()

    def with_product_details(self):
        return self.select_related("product", "user").prefetch_related(
            "product__translations", "product__images", "translations"
        )

    def annotate_user_review_count(self):
        return self.annotate(user_review_count=Count("user__product_reviews"))


class ProductReviewManager(TranslatableManager):
    def get_queryset(self) -> EnhancedReviewQuerySet:
        return EnhancedReviewQuerySet(self.model, using=self._db)

    def approved(self):
        return self.get_queryset().approved()

    def pending(self):
        return self.get_queryset().pending()

    def rejected(self):
        return self.get_queryset().rejected()

    def published(self):
        return self.get_queryset().published()

    def visible(self):
        return self.get_queryset().visible()

    def for_product(self, product):
        return self.get_queryset().for_product(product)

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def by_rate(self, rate):
        return self.get_queryset().by_rate(rate)

    def high_rated(self, min_rate=4):
        return self.get_queryset().high_rated(min_rate)

    def low_rated(self, max_rate=2):
        return self.get_queryset().low_rated(max_rate)

    def recent(self, days=30):
        return self.get_queryset().recent(days)

    def with_comments(self):
        return self.get_queryset().with_comments()

    def without_comments(self):
        return self.get_queryset().without_comments()

    def with_product_details(self):
        return self.get_queryset().with_product_details()

    def annotate_user_review_count(self):
        return self.get_queryset().annotate_user_review_count()
