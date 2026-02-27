from rest_framework import pagination
from rest_framework.response import Response


class PageNumberPaginator(pagination.PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        page = self.page
        assert page is not None
        return Response(
            {
                "links": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "count": page.paginator.count,
                "total_pages": page.paginator.num_pages,
                "page_size": page.paginator.per_page,
                "page_total_results": len(data),
                "page": page.number,
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
                            "example": "http://api.example.org/accounts/?{page_query_param}=4".format(
                                page_query_param=self.page_query_param
                            ),
                        },
                        "previous": {
                            "type": "string",
                            "nullable": True,
                            "format": "uri",
                            "example": "http://api.example.org/accounts/?{page_query_param}=2".format(
                                page_query_param=self.page_query_param
                            ),
                        },
                    },
                },
                "count": {
                    "type": "integer",
                    "example": 123,
                },
                "total_pages": {
                    "type": "integer",
                    "example": 123,
                },
                "page_size": {
                    "type": "integer",
                    "example": 123,
                },
                "page_total_results": {
                    "type": "integer",
                    "example": 123,
                },
                "page": {
                    "type": "integer",
                    "example": 123,
                },
                "results": schema,
            },
        }
