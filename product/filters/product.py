from django_filters import rest_framework as filters

from product.models.product import Product


class ProductFilter(filters.FilterSet):
    min_final_price = filters.NumberFilter(field_name="final_price", lookup_expr="gte")
    max_final_price = filters.NumberFilter(field_name="final_price", lookup_expr="lte")
    category = filters.CharFilter(field_name="category__id", method="filter_category")

    class Meta:
        model = Product
        fields = ["min_final_price", "max_final_price", "category"]

    def filter_category(self, queryset, name, value):
        category_ids = value.split("_")
        return queryset.filter(category__id__in=category_ids)
