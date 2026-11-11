from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import SortableFilterMixin, UUIDFilterMixin
from product.models.favourite import ProductFavourite


class ProductFavouriteFilter(
    SortableFilterMixin, UUIDFilterMixin, CamelCaseTimeStampFilterSet
):
    id = filters.NumberFilter(field_name="id")
    product_id = filters.NumberFilter(field_name="product__id")
    user_id = filters.NumberFilter(field_name="user__id")
    uuid = filters.UUIDFilter(field_name="uuid")

    class Meta:
        model = ProductFavourite
        fields = {
            "id": ["exact"],
            "product": ["exact"],
            "user": ["exact"],
            "uuid": ["exact"],
        }
