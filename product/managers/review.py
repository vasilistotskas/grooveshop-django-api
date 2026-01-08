from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Count, Q
from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet

from product.enum.review import ReviewStatus

if TYPE_CHECKING:
    from typing import Self


class EnhancedReviewQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for ProductReview model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def with_product(self) -> Self:
        """Select related product with translations."""
        return self.select_related("product").prefetch_related(
            "product__translations"
        )

    def with_product_images(self) -> Self:
        """Prefetch product images."""
        return self.prefetch_related("product__images__translations")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user, product, and translations.
        """
        return self.with_translations().with_user().with_product()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus product images.
        """
        return self.for_list().with_product_images()

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
        """Legacy method - use for_detail() instead."""
        return self.for_detail()

    def annotate_user_review_count(self):
        return self.annotate(user_review_count=Count("user__product_reviews"))


class ProductReviewManager(TranslatableManager):
    """
    Manager for ProductReview model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return ProductReview.objects.for_list()
            return ProductReview.objects.for_detail()
    """

    def get_queryset(self) -> EnhancedReviewQuerySet:
        return EnhancedReviewQuerySet(self.model, using=self._db)

    def for_list(self) -> EnhancedReviewQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> EnhancedReviewQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

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
        """Legacy method - use for_detail() instead."""
        return self.get_queryset().for_detail()

    def annotate_user_review_count(self):
        return self.get_queryset().annotate_user_review_count()
