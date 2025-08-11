import math

from rest_framework import pagination
from rest_framework.response import Response


class LimitOffsetPaginator(pagination.LimitOffsetPagination):
    max_limit = 100

    def get_paginated_response(self, data):
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
                            "example": "http://api.example.org/accounts/?{limit_query_param}=2&{offset_query_param}=4".format(
                                limit_query_param=self.limit_query_param,
                                offset_query_param=self.offset_query_param,
                            ),
                        },
                        "previous": {
                            "type": "string",
                            "nullable": True,
                            "format": "uri",
                            "example": "http://api.example.org/accounts/?{limit_query_param}=2&{offset_query_param}=0".format(
                                limit_query_param=self.limit_query_param,
                                offset_query_param=self.offset_query_param,
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
