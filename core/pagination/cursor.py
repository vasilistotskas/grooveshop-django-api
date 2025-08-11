import math

from rest_framework import pagination
from rest_framework.response import Response


class CursorPaginator(pagination.CursorPagination):
    page_size = 100
    max_page_size = 100
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
    total_items = 0
    total_pages = 0
    ordering = "-created_at"
    has_next = False
    has_previous = False

    def paginate_queryset(self, queryset, request, view=None):
        self.total_items = queryset.count()
        self.total_pages = self.get_total_pages()
        super().paginate_queryset(queryset, request, view)
        return self.page

    def get_total_pages(self):
        total_pages = math.ceil(self.total_items / self.page_size)
        if self.total_items % self.page_size != 0:
            total_pages += 1
        return total_pages

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
