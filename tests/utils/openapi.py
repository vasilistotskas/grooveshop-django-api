"""Validate a real response body against the published OpenAPI document.

The storefront generates its Zod schemas from ``schema.yml`` and answers
422 to any body they reject, so the useful check is the storefront's:
does this JSON satisfy the operation's documented 200 response? The
document is OpenAPI 3.0, whose ``nullable`` JSON Schema does not know;
``as_json_schema`` rewrites it into ``"null"`` alternatives and inlines
every ``$ref`` so ``jsonschema`` can judge it. The rest is JSON Schema
draft 4's vocabulary (a boolean ``exclusiveMaximum``), so draft 4 judges.
"""

from __future__ import annotations

from typing import Any

import jsonschema


def as_json_schema(node: Any, components: dict) -> Any:
    if isinstance(node, list):
        return [as_json_schema(item, components) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return as_json_schema(components[name], components)
    converted = {
        key: as_json_schema(value, components)
        for key, value in node.items()
        if key != "nullable"
    }
    if node.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


def response_schema(openapi: dict, path: str, method: str = "get") -> dict:
    content = openapi["paths"][path][method]["responses"]["200"]["content"]
    return content["application/json"]["schema"]


def assert_matches_response(
    openapi: dict, path: str, body: Any, method: str = "get"
) -> None:
    """Raise if ``body`` is not what ``path``'s 200 response documents."""
    schema = as_json_schema(
        response_schema(openapi, path, method),
        openapi["components"]["schemas"],
    )
    jsonschema.Draft4Validator(schema).validate(body)
