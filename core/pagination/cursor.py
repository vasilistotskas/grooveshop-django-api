import math

from rest_framework import pagination
from rest_framework.response import Response


class CursorPaginator(pagination.CursorPagination):
    page_size = 20
    max_page_size = 100
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
    total_items = 0
    total_pages = 0
    ordering = "-created_at"
    has_next = False
    has_previous = False

    def paginate_queryset(self, queryset, request, view=None):
        page_size_param = request.query_params.get(
            self.page_size_query_param
        ) or request.query_params.get("page_size")
        if page_size_param:
            try:
                self.page_size = min(int(page_size_param), self.max_page_size)
            except (ValueError, TypeError):
                pass

        self.total_items = queryset.count()

        page = super().paginate_queryset(queryset, request, view)
        self.page = page

        self.total_pages = self.get_total_pages()

        return page

    def get_total_pages(self):
        if self.total_items == 0:
            return 1
        actual_page_size = getattr(self, "page_size", 20)
        return math.ceil(self.total_items / actual_page_size)

    def get_paginated_response(self, data):
        return Response(
            {
                "links": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "count": self.total_items,
                "total_pages": self.total_pages,
                "page_size": self.page_size,
                "page_total_results": len(data),
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "results"],
            "properties": {
                "links": {
                    "type": "object",
                    "properties": {
                        "next": {
                            "type": "string",
                            "nullable": True,
                            "format": "uri",
                        },
                        "previous": {
                            "type": "string",
                            "nullable": True,
                            "format": "uri",
                        },
                    },
                },
                "count": {"type": "integer", "example": 123},
                "total_pages": {"type": "integer", "example": 123},
                "page_size": {"type": "integer", "example": 123},
                "page_total_results": {"type": "integer", "example": 123},
                "results": schema,
            },
        }
