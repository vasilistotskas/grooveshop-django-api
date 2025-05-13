import re
from typing import override

from rest_framework.filters import OrderingFilter


class PascalSnakeCaseOrderingFilter(OrderingFilter):
    # Map property names to annotated field names
    field_name_mapping = {
        "discount_value": "discount_value_amount",
        "final_price": "final_price_amount",
        "price_save_percent": "price_save_percent_field",
        "review_average": "review_average_field",
        "approved_review_average": "approved_review_average_field",
        "likes_count": "likes_count_field",
    }

    @override
    def get_ordering(self, request, queryset, view):
        if "query_params" not in dir(request):
            return self.get_default_ordering(view)

        ordering = request.query_params.get(self.ordering_param)
        if ordering:
            # Convert camelCase or PascalCase to snake_case
            ordering = (
                re.sub("([a-z])([A-Z])", r"\1_\2", ordering).lower().lstrip("_")
            )

            # Map virtual fields to annotated fields
            ordering_terms = []
            for term in ordering.split(","):
                stripped_term = term.lstrip("-")
                if stripped_term in self.field_name_mapping:
                    prefix = "-" if term.startswith("-") else ""
                    ordering_terms.append(
                        f"{prefix}{self.field_name_mapping[stripped_term]}"
                    )
                else:
                    ordering_terms.append(term)

            return ordering_terms

        return self.get_default_ordering(view)
