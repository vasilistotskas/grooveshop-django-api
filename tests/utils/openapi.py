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


def response_schema(
    openapi: dict, path: str, method: str = "get", status: str = "200"
) -> dict:
    content = openapi["paths"][path][method]["responses"][status]["content"]
    return content["application/json"]["schema"]


def assert_matches_response(
    openapi: dict,
    path: str,
    body: Any,
    method: str = "get",
    status: str = "200",
) -> None:
    """Raise if ``body`` is not what ``path``'s ``status`` response (200 by
    default) documents."""
    schema = as_json_schema(
        response_schema(openapi, path, method, status),
        openapi["components"]["schemas"],
    )
    jsonschema.Draft4Validator(schema).validate(body)


def validate_against(openapi: dict, node: Any, body: Any) -> None:
    """Raise if ``body`` does not satisfy ``node``, a schema of ``openapi``.

    Unlike :func:`as_json_schema` this keeps ``$ref`` as a reference to the
    converted components, so a schema that refers to itself (a field error
    holding field errors) can be judged.
    """

    def convert(value: Any) -> Any:
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        converted = {
            key: (
                "#/definitions/" + item.rsplit("/", 1)[-1]
                if key == "$ref" and isinstance(item, str)
                else convert(item)
            )
            for key, item in value.items()
            if key != "nullable"
        }
        if value.get("nullable"):
            return {"anyOf": [converted, {"type": "null"}]}
        return converted

    schema = convert(node)
    schema["definitions"] = {
        name: convert(component)
        for name, component in openapi["components"]["schemas"].items()
    }
    jsonschema.Draft4Validator(schema).validate(body)
