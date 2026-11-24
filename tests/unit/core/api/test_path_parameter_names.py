"""A path parameter is named as its URL template names it.

OpenAPI requires a path parameter's ``name`` to equal the template
expression it fills (``/country/{alpha_2}`` takes ``alpha_2``). The
camel-case hook renames every parameter for the request middleware, which
rewrites query names but never the URL, so a strict client either
rejects the document or cannot fill the placeholder (issue #814).
"""

from __future__ import annotations

import re

TEMPLATE_EXPRESSION = re.compile(r"\{([^}]+)\}")


def test_every_path_parameter_names_a_template_expression(openapi_schema):
    mismatched = [
        f"{method.upper()} {path}: {param['name']}"
        for path, methods in openapi_schema["paths"].items()
        for method, operation in methods.items()
        if isinstance(operation, dict)
        for param in operation.get("parameters", [])
        if param["in"] == "path"
        and param["name"] not in TEMPLATE_EXPRESSION.findall(path)
    ]
    assert mismatched == []


def test_every_template_expression_has_its_path_parameter(openapi_schema):
    missing = [
        f"{method.upper()} {path}: {expression}"
        for path, methods in openapi_schema["paths"].items()
        for method, operation in methods.items()
        if isinstance(operation, dict)
        for expression in TEMPLATE_EXPRESSION.findall(path)
        if expression
        not in {
            param["name"]
            for param in operation.get("parameters", [])
            if param["in"] == "path"
        }
    ]
    assert missing == []


def test_query_parameters_stay_camel_case(openapi_schema):
    """The middleware underscores query names, so the document keeps
    them camel-cased: only path parameters follow their template."""
    names = {
        param["name"]
        for methods in openapi_schema["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict)
        for param in operation.get("parameters", [])
        if param["in"] == "query"
    }
    assert "pageSize" in names
    assert not any("_" in name and name.lower() == name for name in names)
