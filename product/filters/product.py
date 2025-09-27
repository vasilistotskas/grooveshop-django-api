from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import (
    UUIDFilterMixin,
    SoftDeleteFilterMixin,
    MetaDataFilterMixin,
)
from product.models.category import ProductCategory
from product.models.product import Product


class ProductFilter(
    UUIDFilterMixin,
    SoftDeleteFilterMixin,
    MetaDataFilterMixin,
    CamelCaseTimeStampFilterSet,
):
    id = filters.NumberFilter(field_name="id")
    sku = filters.CharFilter(field_name="sku", lookup_expr="icontains")
    active = filters.BooleanFilter(field_name="active")

    min_price = filters.NumberFilter(
        field_name="price",
        lookup_expr="gte",
        help_text="Minimum price",
    )
    max_price = filters.NumberFilter(
        field_name="price",
        lookup_expr="lte",
        help_text="Maximum price",
    )
    min_final_price = filters.NumberFilter(
        method="filter_min_final_price",
        help_text="Minimum final price (after VAT and discount)",
    )
    max_final_price = filters.NumberFilter(
        method="filter_max_final_price",
        help_text="Maximum final price (after VAT and discount)",
    )

    min_stock = filters.NumberFilter(
        field_name="stock",
        lookup_expr="gte",
        help_text="Minimum stock quantity",
    )
    max_stock = filters.NumberFilter(
        field_name="stock",
        lookup_expr="lte",
        help_text="Maximum stock quantity",
    )
    in_stock = filters.BooleanFilter(
        method="filter_in_stock",
        help_text="Filter products that are in stock (stock > 0)",
    )

    min_discount_percent = filters.NumberFilter(
        field_name="discount_percent",
        lookup_expr="gte",
        help_text="Minimum discount percentage",
    )
    max_discount_percent = filters.NumberFilter(
        field_name="discount_percent",
        lookup_expr="lte",
        help_text="Maximum discount percentage",
    )
    min_discount = filters.NumberFilter(
        method="filter_min_discount",
        help_text="Minimum discount value amount",
    )
    max_discount = filters.NumberFilter(
        method="filter_max_discount",
        help_text="Maximum discount value amount",
    )
    has_discount = filters.BooleanFilter(
        method="filter_has_discount",
        help_text="Filter products that have a discount",
    )

    category = filters.CharFilter(
        field_name="category__id",
        method="filter_category",
        help_text="Filter by category ID (supports multiple IDs separated by underscore)",
    )
    category_id = filters.NumberFilter(
        field_name="category__id",
        help_text="Filter by exact category ID",
    )

    min_view_count = filters.NumberFilter(
        field_name="view_count",
        lookup_expr="gte",
        help_text="Minimum view count",
    )
    max_view_count = filters.NumberFilter(
        field_name="view_count",
        lookup_expr="lte",
        help_text="Maximum view count",
    )

    min_review_average = filters.NumberFilter(
        method="filter_min_review_average",
        help_text="Minimum review average rating",
    )
    max_review_average = filters.NumberFilter(
        method="filter_max_review_average",
        help_text="Maximum review average rating",
    )

    min_likes = filters.NumberFilter(
        method="filter_min_likes",
        help_text="Minimum likes count",
    )
    max_likes = filters.NumberFilter(
        method="filter_max_likes",
        help_text="Maximum likes count",
    )

    min_weight = filters.NumberFilter(
        field_name="weight",
        lookup_expr="gte",
        help_text="Minimum weight",
    )
    max_weight = filters.NumberFilter(
        field_name="weight",
        lookup_expr="lte",
        help_text="Maximum weight",
    )

    class Meta:
        model = Product
        fields = {
            "id": ["exact"],
            "sku": ["exact", "icontains"],
            "active": ["exact"],
            "price": ["exact", "gte", "lte"],
            "stock": ["exact", "gte", "lte"],
            "discount_percent": ["exact", "gte", "lte"],
            "view_count": ["exact", "gte", "lte"],
            "weight": ["exact", "gte", "lte"],
            "category": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "deleted_at": ["gte", "lte", "date"],
            "is_deleted": ["exact"],
            "uuid": ["exact"],
        }

    def filter_category(self, queryset, name, value):
        """Filter by category including descendant categories"""
        category_ids = value.split("_")
        all_relevant_category_ids = []

        for category_id in category_ids:
            try:
                category = ProductCategory.objects.get(id=category_id)
                descendant_ids = category.get_descendants(
                    include_self=True
                ).values_list("id", flat=True)
                all_relevant_category_ids.extend(descendant_ids)
            except ProductCategory.DoesNotExist:
                pass

        return queryset.filter(category__id__in=all_relevant_category_ids)

    def filter_in_stock(self, queryset, name, value):
        """Filter products that are in stock"""
        if value:
            return queryset.filter(stock__gt=0)
        return queryset.filter(stock=0)

    def filter_has_discount(self, queryset, name, value):
        """Filter products that have a discount"""
        if value:
            return queryset.filter(discount_percent__gt=0)
        return queryset.filter(discount_percent=0)

    def filter_min_final_price(self, queryset, name, value):
        """Filter products with minimum final price"""
        if value is not None:
            return queryset.with_final_price_annotation().filter(
                final_price_annotation__gte=value
            )
        return queryset

    def filter_max_final_price(self, queryset, name, value):
        """Filter products with maximum final price"""
        if value is not None:
            return queryset.with_final_price_annotation().filter(
                final_price_annotation__lte=value
            )
        return queryset

    def filter_min_discount(self, queryset, name, value):
        """Filter products with minimum discount value"""
        if value is not None:
            return queryset.with_discount_value_annotation().filter(
                discount_value_annotation__gte=value
            )
        return queryset

    def filter_max_discount(self, queryset, name, value):
        """Filter products with maximum discount value"""
        if value is not None:
            return queryset.with_discount_value_annotation().filter(
                discount_value_annotation__lte=value
            )
        return queryset

    def filter_min_review_average(self, queryset, name, value):
        """Filter products with minimum review average"""
        if value is not None:
            return queryset.with_review_average_annotation().filter(
                review_average_annotation__gte=value
            )
        return queryset

    def filter_max_review_average(self, queryset, name, value):
        """Filter products with maximum review average"""
        if value is not None:
            return queryset.with_review_average_annotation().filter(
                review_average_annotation__lte=value
            )
        return queryset

    def filter_min_likes(self, queryset, name, value):
        """Filter products with minimum likes count"""
        if value is not None:
            return queryset.with_likes_count_annotation().filter(
                likes_count_annotation__gte=value
            )
        return queryset

    def filter_max_likes(self, queryset, name, value):
        """Filter products with maximum likes count"""
        if value is not None:
            return queryset.with_likes_count_annotation().filter(
                likes_count_annotation__lte=value
            )
        return queryset
