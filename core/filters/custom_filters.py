import re
from typing import override

from rest_framework.filters import OrderingFilter


class PascalSnakeCaseOrderingFilter(OrderingFilter):
    @override
    def get_ordering(self, request, queryset, view):
        if "query_params" not in dir(request):
            return self.get_default_ordering(view)

        ordering = request.query_params.get(self.ordering_param)
        if ordering:
            ordering = re.sub("([a-z])([A-Z])", r"\1_\2", ordering).lower().lstrip("_")
            return [term for term in ordering.split(",")]
        return self.get_default_ordering(view)
