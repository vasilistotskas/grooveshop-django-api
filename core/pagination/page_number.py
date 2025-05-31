from rest_framework import pagination
from rest_framework.response import Response


class PageNumberPaginator(pagination.PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "links": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "count": self.page.paginator.count,
                "total_pages": self.page.paginator.num_pages,
                "page_size": self.page.paginator.per_page,
                "page_total_results": len(data),
                "page": self.page.number,
                "results": data,
            }
        )
