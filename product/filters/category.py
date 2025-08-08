from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import (
    UUIDFilterMixin,
    SortableFilterMixin,
)
from product.models.category import ProductCategory


class ProductCategoryFilter(
    UUIDFilterMixin,
    SortableFilterMixin,
    CamelCaseTimeStampFilterSet,
):
    id = filters.NumberFilter(field_name="id")
    slug = filters.CharFilter(field_name="slug", lookup_expr="icontains")
    active = filters.BooleanFilter(field_name="active")

    sort_order = filters.NumberFilter(
        field_name="sort_order",
        help_text="Filter by exact sort order",
    )
    sort_order_min = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="gte",
        help_text="Minimum sort order",
    )
    sort_order_max = filters.NumberFilter(
        field_name="sort_order",
        lookup_expr="lte",
        help_text="Maximum sort order",
    )

    parent = filters.NumberFilter(
        field_name="parent__id",
        help_text="Filter by parent category ID",
    )
    parent_slug = filters.CharFilter(
        field_name="parent__slug",
        help_text="Filter by parent category slug",
    )
    level = filters.NumberFilter(
        field_name="level",
        help_text="Filter by hierarchy level (0 = root)",
    )
    min_level = filters.NumberFilter(
        field_name="level",
        lookup_expr="gte",
        help_text="Minimum hierarchy level",
    )
    max_level = filters.NumberFilter(
        field_name="level",
        lookup_expr="lte",
        help_text="Maximum hierarchy level",
    )

    is_root = filters.BooleanFilter(
        method="filter_is_root",
        help_text="Filter root categories (no parent)",
    )
    is_leaf = filters.BooleanFilter(
        method="filter_is_leaf",
        help_text="Filter leaf categories (no children)",
    )
    has_children = filters.BooleanFilter(
        method="filter_has_children",
        help_text="Filter categories that have children",
    )

    min_product_count = filters.NumberFilter(
        method="filter_min_product_count",
        help_text="Minimum number of products (recursive)",
    )
    max_product_count = filters.NumberFilter(
        method="filter_max_product_count",
        help_text="Maximum number of products (recursive)",
    )
    has_products = filters.BooleanFilter(
        method="filter_has_products",
        help_text="Filter categories that have products (recursive)",
    )

    ancestor_of = filters.NumberFilter(
        method="filter_ancestor_of",
        help_text="Filter ancestors of specified category ID",
    )
    descendant_of = filters.NumberFilter(
        method="filter_descendant_of",
        help_text="Filter descendants of specified category ID",
    )
    sibling_of = filters.NumberFilter(
        method="filter_sibling_of",
        help_text="Filter siblings of specified category ID",
    )

    class Meta:
        model = ProductCategory
        fields = {
            "id": ["exact"],
            "slug": ["exact", "icontains"],
            "active": ["exact"],
            "parent": ["exact"],
            "level": ["exact", "gte", "lte"],
            "sort_order": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }

    def filter_is_root(self, queryset, name, value):
        """Filter root categories (no parent)"""
        if value:
            return queryset.filter(parent__isnull=True)
        return queryset.filter(parent__isnull=False)

    def filter_is_leaf(self, queryset, name, value):
        """Filter leaf categories (no children)"""
        if value:
            return queryset.filter(children__isnull=True)
        return queryset.filter(children__isnull=False)

    def filter_has_children(self, queryset, name, value):
        """Filter categories that have children"""
        if value:
            return queryset.filter(children__isnull=False).distinct()
        return queryset.filter(children__isnull=True)

    def filter_min_product_count(self, queryset, name, value):
        """Filter categories with minimum product count (recursive)"""
        if not value or value <= 0:
            return queryset

        from product.models.product import Product

        category_ids = []
        for category in queryset:
            descendants = category.get_descendants(include_self=True)
            product_count = Product.objects.filter(
                category__in=descendants
            ).count()
            if product_count >= value:
                category_ids.append(category.id)

        return queryset.filter(id__in=category_ids)

    def filter_max_product_count(self, queryset, name, value):
        """Filter categories with maximum product count (recursive)"""
        if not value or value < 0:
            return queryset

        from product.models.product import Product

        category_ids = []
        for category in queryset:
            descendants = category.get_descendants(include_self=True)
            product_count = Product.objects.filter(
                category__in=descendants
            ).count()
            if product_count <= value:
                category_ids.append(category.id)

        return queryset.filter(id__in=category_ids)

    def filter_has_products(self, queryset, name, value):
        """Filter categories that have products (recursive)"""
        from product.models.product import Product

        category_ids = []
        for category in queryset:
            descendants = category.get_descendants(include_self=True)
            has_products = Product.objects.filter(
                category__in=descendants
            ).exists()
            if has_products == value:
                category_ids.append(category.id)

        return queryset.filter(id__in=category_ids)

    def filter_ancestor_of(self, queryset, name, value):
        """Filter ancestors of specified category"""
        try:
            category = ProductCategory.objects.get(id=value)
            ancestors = category.get_ancestors()
            return queryset.filter(
                id__in=ancestors.values_list("id", flat=True)
            )
        except ProductCategory.DoesNotExist:
            return queryset.none()

    def filter_descendant_of(self, queryset, name, value):
        """Filter descendants of specified category"""
        try:
            category = ProductCategory.objects.get(id=value)
            descendants = category.get_descendants()
            return queryset.filter(
                id__in=descendants.values_list("id", flat=True)
            )
        except ProductCategory.DoesNotExist:
            return queryset.none()

    def filter_sibling_of(self, queryset, name, value):
        """Filter siblings of specified category"""
        try:
            category = ProductCategory.objects.get(id=value)
            siblings = category.get_siblings()
            return queryset.filter(id__in=siblings.values_list("id", flat=True))
        except ProductCategory.DoesNotExist:
            return queryset.none()
