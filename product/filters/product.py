from django_filters import rest_framework as filters

from product.models.category import ProductCategory
from product.models.product import Product


class ProductFilter(filters.FilterSet):
    min_final_price = filters.NumberFilter(
        field_name="final_price_amount",
        lookup_expr="gte",
        label="Minimum Final Price",
    )
    max_final_price = filters.NumberFilter(
        field_name="final_price_amount",
        lookup_expr="lte",
        label="Maximum Final Price",
    )
    category = filters.CharFilter(
        field_name="category__id", method="filter_category", label="Category"
    )
    min_discount = filters.NumberFilter(
        field_name="discount_value_amount",
        lookup_expr="gte",
        label="Minimum Discount Value",
    )
    max_discount = filters.NumberFilter(
        field_name="discount_value_amount",
        lookup_expr="lte",
        label="Maximum Discount Value",
    )
    min_review_average = filters.NumberFilter(
        field_name="review_average_field",
        lookup_expr="gte",
        label="Minimum Review Average",
    )
    min_likes = filters.NumberFilter(
        field_name="likes_count_field",
        lookup_expr="gte",
        label="Minimum Likes Count",
    )

    class Meta:
        model = Product
        fields = [
            "active",
            "min_final_price",
            "max_final_price",
            "category",
            "min_discount",
            "max_discount",
            "min_review_average",
            "min_likes",
        ]

    def filter_category(self, queryset, name, value):
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
