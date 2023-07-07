import re

from rest_framework.filters import OrderingFilter


class PascalSnakeCaseOrderingFilter(OrderingFilter):
    def get_ordering(self, request, queryset, view):
        ordering = request.query_params.get(self.ordering_param)
        if ordering:
            ordering = re.sub("([A-Z]+)", r"_\1", ordering).lower().lstrip("_")
            return [term for term in ordering.split(",")]
        return self.get_default_ordering(view)
