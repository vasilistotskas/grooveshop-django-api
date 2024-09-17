import math
from typing import override

from rest_framework import pagination
from rest_framework.response import Response


class LimitOffsetPaginator(pagination.LimitOffsetPagination):
    max_limit = 100

    @override
    def get_paginated_response(self, data) -> Response:
        return Response(
            {
                "links": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "count": self.count,
                "total_pages": math.ceil(self.count / self.limit),
                "page_size": self.limit,
                "page_total_results": len(data),
                "page": math.ceil(self.offset / self.limit) + 1,
                "results": data,
            }
        )
