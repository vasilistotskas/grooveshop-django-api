from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from product.enum.review import RateEnum, ReviewStatus
from product.models.review import ProductReview


class ProductReviewFilter(filters.FilterSet):
    id = filters.NumberFilter(
        field_name="id",
        lookup_expr="exact",
        help_text=_("Filter by review ID"),
    )
    user = filters.NumberFilter(
        field_name="user_id",
        lookup_expr="exact",
        help_text=_("Filter by user ID"),
    )
    product = filters.NumberFilter(
        field_name="product_id",
        lookup_expr="exact",
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
        help_text=_("Filter by rating"),
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
    is_published = filters.BooleanFilter(
        field_name="is_published",
        help_text=_("Filter by published status"),
    )
    is_visible = filters.BooleanFilter(
        field_name="is_visible",
        help_text=_("Filter by visibility status"),
    )
    created_after = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text=_("Filter reviews created after this date"),
    )
    created_before = filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text=_("Filter reviews created before this date"),
    )
    published_after = filters.DateTimeFilter(
        field_name="published_at",
        lookup_expr="gte",
        help_text=_("Filter reviews published after this date"),
    )
    published_before = filters.DateTimeFilter(
        field_name="published_at",
        lookup_expr="lte",
        help_text=_("Filter reviews published before this date"),
    )
    comment = filters.CharFilter(
        field_name="translations__comment",
        lookup_expr="icontains",
        help_text=_("Filter by comment content (partial match)"),
    )
    product_name = filters.CharFilter(
        field_name="product__translations__name",
        lookup_expr="icontains",
        help_text=_("Filter by product name (partial match)"),
    )
    user_email = filters.CharFilter(
        field_name="user__email",
        lookup_expr="icontains",
        help_text=_("Filter by user email (partial match)"),
    )
    has_comment = filters.BooleanFilter(
        method="filter_has_comment",
        help_text=_("Filter reviews that have/don't have comments"),
    )
    recent_days = filters.NumberFilter(
        method="filter_recent_days",
        help_text=_("Filter reviews from the last N days"),
    )

    class Meta:
        model = ProductReview
        fields = [
            "id",
            "user",
            "product",
            "status",
            "rate",
            "rate_min",
            "rate_max",
            "is_published",
            "is_visible",
            "created_after",
            "created_before",
            "published_after",
            "published_before",
            "comment",
            "product_name",
            "user_email",
            "has_comment",
            "recent_days",
        ]

    def filter_has_comment(self, queryset, name, value):
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
        if value and value > 0:
            cutoff_date = timezone.now() - timedelta(days=value)
            return queryset.filter(created_at__gte=cutoff_date)
        return queryset
