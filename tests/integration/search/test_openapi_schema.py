"""The search endpoints' OpenAPI contract, as the storefront consumes it.

The Nuxt storefront generates its request and response Zod schemas from
this document (``pnpm openapi-ts``). Each assertion names what that
generation needs; removing any of it breaks the storefront, adding to it
does not, so every check is "at least these".
"""

from __future__ import annotations

import pytest

SEARCH_ENDPOINTS = {
    "/api/v1/search/federated": {
        "params": {"query", "languageCode", "limit", "offset"},
        "response": (
            "FederatedSearchResponse",
            {"results", "limit", "offset", "estimatedTotalHits", "queryId"},
        ),
    },
    "/api/v1/search/analytics": {
        "params": {"startDate", "endDate", "contentType"},
        "response": (
            "SearchAnalyticsResponse",
            {
                "searchVolume",
                "topQueries",
                "zeroResultQueries",
                "clickThroughRate",
                "performance",
                "dateRange",
            },
        ),
    },
    "/api/v1/search/product": {
        "params": {"query", "languageCode", "limit", "offset", "sort"},
        "response": (
            "ProductMeiliSearchResponse",
            {"results", "limit", "offset", "facetDistribution", "queryId"},
        ),
    },
    "/api/v1/search/blog/post": {
        "params": {"query", "languageCode", "limit", "offset"},
        "response": (
            "BlogPostMeiliSearchResponse",
            {"results", "limit", "offset", "estimatedTotalHits", "queryId"},
        ),
    },
}

pytestmark = pytest.mark.parametrize("endpoint", SEARCH_ENDPOINTS)


def _get(openapi_schema, endpoint):
    return openapi_schema["paths"][endpoint]["get"]


def _json_schema(response):
    return response["content"]["application/json"]["schema"]


def test_query_parameters_are_described(openapi_schema, endpoint):
    parameters = _get(openapi_schema, endpoint)["parameters"]

    assert {p["name"] for p in parameters} >= SEARCH_ENDPOINTS[endpoint][
        "params"
    ]
    for parameter in parameters:
        assert parameter["in"] == "query", parameter["name"]
        assert "schema" in parameter, parameter["name"]
        assert parameter["description"], parameter["name"]


def test_success_response_is_a_named_component(openapi_schema, endpoint):
    component, fields = SEARCH_ENDPOINTS[endpoint]["response"]

    assert _json_schema(_get(openapi_schema, endpoint)["responses"]["200"]) == {
        "$ref": f"#/components/schemas/{component}"
    }
    properties = openapi_schema["components"]["schemas"][component][
        "properties"
    ]
    assert set(properties) >= fields


def test_validation_error_response_is_declared(openapi_schema, endpoint):
    assert _json_schema(_get(openapi_schema, endpoint)["responses"]["400"]) == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
