from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import (
    CamelCasePublishableTimeStampFilterSet,
)
from core.filters.core import UUIDFilterMixin
from product.enum.review import RateEnum, ReviewStatus
from product.models.review import ProductReview


class ProductReviewFilter(
    UUIDFilterMixin,
    CamelCasePublishableTimeStampFilterSet,
):
    id = filters.NumberFilter(
        field_name="id",
        help_text=_("Filter by review ID"),
    )
    user = filters.NumberFilter(
        field_name="user_id",
        help_text=_("Filter by user ID"),
    )
    user_id = filters.NumberFilter(
        field_name="user_id",
        help_text=_("Filter by user ID"),
    )
    product = filters.NumberFilter(
        field_name="product_id",
        help_text=_("Filter by product ID"),
    )
    product_id = filters.NumberFilter(
        field_name="product_id",
        help_text=_("Filter by product ID"),
    )

    status = filters.ChoiceFilter(
        field_name="status",
        choices=ReviewStatus.choices,
        help_text=_("Filter by review status"),
    )

    rate = filters.ChoiceFilter(
        field_name="rate",
        choices=RateEnum.choices,
        help_text=_("Filter by exact rating"),
    )
    rate_min = filters.NumberFilter(
        field_name="rate",
        lookup_expr="gte",
        help_text=_("Filter by minimum rating"),
    )
    rate_max = filters.NumberFilter(
        field_name="rate",
        lookup_expr="lte",
        help_text=_("Filter by maximum rating"),
    )
    min_rate = filters.NumberFilter(
        field_name="rate",
        lookup_expr="gte",
        help_text=_("Filter by minimum rating (alias)"),
    )
    max_rate = filters.NumberFilter(
        field_name="rate",
        lookup_expr="lte",
        help_text=_("Filter by maximum rating (alias)"),
    )

    comment = filters.CharFilter(
        field_name="translations__comment",
        lookup_expr="icontains",
        help_text=_("Filter by comment content (partial match)"),
    )
    has_comment = filters.BooleanFilter(
        method="filter_has_comment",
        help_text=_("Filter reviews that have/don't have comments"),
    )

    product_name = filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (partial match)"),
    )
    product_category = filters.NumberFilter(
        field_name="product__category_id",
        help_text=_("Filter by product category ID"),
    )
    product_active = filters.BooleanFilter(
        field_name="product__active",
        help_text=_("Filter by product active status"),
    )
    user_email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
    )
    user_first_name = filters.CharFilter(
        field_name="user__first_name",
        lookup_expr="icontains",
        help_text=_("Filter by user first name (partial match)"),
    )
    user_last_name = filters.CharFilter(
        field_name="user__last_name",
        lookup_expr="icontains",
        help_text=_("Filter by user last name (partial match)"),
    )

    recent_days = filters.NumberFilter(
        method="filter_recent_days",
        help_text=_("Filter reviews from the last N days"),
    )
    published_recent_days = filters.NumberFilter(
        method="filter_published_recent_days",
        help_text=_("Filter reviews published in the last N days"),
    )

    verified_purchase = filters.BooleanFilter(
        method="filter_verified_purchase",
        help_text=_(
            "Filter reviews from verified purchases (if order system exists)"
        ),
    )
    helpful_votes_min = filters.NumberFilter(
        method="filter_helpful_votes_min",
        help_text=_(
            "Filter by minimum helpful votes (if voting system exists)"
        ),
    )

    user_review_count_min = filters.NumberFilter(
        method="filter_user_review_count_min",
        help_text=_("Filter users with minimum number of reviews"),
    )
    product_avg_rating_min = filters.NumberFilter(
        method="filter_product_avg_rating_min",
        help_text=_("Filter products with minimum average rating"),
    )
    product_avg_rating_max = filters.NumberFilter(
        method="filter_product_avg_rating_max",
        help_text=_("Filter products with maximum average rating"),
    )

    class Meta:
        model = ProductReview
        fields = {
            "id": ["exact"],
            "user": ["exact"],
            "product": ["exact"],
            "status": ["exact"],
            "rate": ["exact", "gte", "lte"],
            "is_published": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "published_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def filter_has_comment(self, queryset, name, value):
        """Filter reviews that have/don't have comments"""
        if value is True:
            return queryset.exclude(
                models.Q(translations__comment__isnull=True)
                | models.Q(translations__comment__exact="")
            ).distinct()
        elif value is False:
            return queryset.filter(
                models.Q(translations__comment__isnull=True)
                | models.Q(translations__comment__exact="")
            ).distinct()
        return queryset

    def filter_recent_days(self, queryset, name, value):
        """Filter reviews from the last N days"""
        if value and value > 0:
            cutoff_date = timezone.now() - timedelta(days=int(value))
            return queryset.filter(created_at__gte=cutoff_date)
        return queryset

    def filter_published_recent_days(self, queryset, name, value):
        """Filter reviews published in the last N days"""
        if value and value > 0:
            cutoff_date = timezone.now() - timedelta(days=int(value))
            return queryset.filter(
                published_at__gte=cutoff_date, published_at__isnull=False
            )
        return queryset

    def filter_verified_purchase(self, queryset, name, value):
        """Filter reviews from verified purchases (placeholder for order system)"""
        # This would need to be implemented when order system exists
        # For now, just return the queryset as-is
        # In a real system, this might check:
        # return queryset.filter(user__orders__products=models.F('product'))
        return queryset

    def filter_helpful_votes_min(self, queryset, name, value):
        """Filter by minimum helpful votes (placeholder for voting system)"""
        # This would need to be implemented when review voting system exists
        # For now, just return the queryset as-is
        return queryset

    def filter_user_review_count_min(self, queryset, name, value):
        """Filter users with minimum number of reviews"""
        if value and value > 0:
            from django.db.models import Count

            users_with_min_reviews = (
                ProductReview.objects.values("user_id")
                .annotate(review_count=Count("id"))
                .filter(review_count__gte=value)
                .values_list("user_id", flat=True)
            )

            return queryset.filter(user_id__in=users_with_min_reviews)
        return queryset

    def filter_product_avg_rating_min(self, queryset, name, value):
        """Filter products with minimum average rating"""
        if value and value > 0:
            from django.db.models import Avg

            products_with_min_avg = (
                ProductReview.objects.values("product_id")
                .annotate(avg_rating=Avg("rate"))
                .filter(avg_rating__gte=value)
                .values_list("product_id", flat=True)
            )

            return queryset.filter(product_id__in=products_with_min_avg)
        return queryset

    def filter_product_avg_rating_max(self, queryset, name, value):
        """Filter products with maximum average rating"""
        if value and value > 0:
            from django.db.models import Avg

            products_with_max_avg = (
                ProductReview.objects.values("product_id")
                .annotate(avg_rating=Avg("rate"))
                .filter(avg_rating__lte=value)
                .values_list("product_id", flat=True)
            )

            return queryset.filter(product_id__in=products_with_max_avg)
        return queryset
