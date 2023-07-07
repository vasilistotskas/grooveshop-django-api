import math

from rest_framework import pagination
from rest_framework.response import Response


class CursorPaginator(pagination.CursorPagination):
    page_size = 100
    cursor_query_param = "c"
    total_items = 0
    total_pages = 0
    current_page_number = 0

    def paginate_queryset(self, queryset, request, view=None):
        self.total_items = queryset.count()
        self.total_pages = self.get_total_pages()
        super().paginate_queryset(queryset, request, view)
        self.current_page_number = self.get_current_page_number(request)
        return self.page

    def get_total_pages(self) -> int:
        total_pages = math.ceil(self.total_items / self.page_size)
        if self.total_items % self.page_size != 0:
            total_pages += 1
        return total_pages

    # @TODO: This is not working properly.
    def get_current_page_number(self, request) -> int:
        cursor = self.decode_cursor(request)
        if cursor is None:
            return 1
        else:
            cursor_offset = cursor[0]
            current_page = cursor_offset / self.page_size + 1
            return current_page

    def get_paginated_response(self, data) -> Response:
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
                "page": self.current_page_number,
                "results": data,
            }
        )
