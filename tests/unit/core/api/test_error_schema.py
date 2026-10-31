"""The schema documents each 400 body the handler really sends."""

import copy

import pytest
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from rest_framework import status
from rest_framework.test import APIClient

from core.api.schema import (
    ERROR_SCHEMAS,
    VALIDATION_OR_PLAIN_ERROR,
    publish_error_schemas,
)

GENERIC = {"$ref": "#/components/schemas/ErrorResponse"}
SPECIFIC = {"$ref": "#/components/schemas/B2BErrorResponse"}
DOCUMENT_URI = "urn:openapi"


def _operation(schema):
    return {
        "responses": {
            "400": {"content": {"application/json": {"schema": schema}}}
        }
    }


def _documented_400(result, path, method):
    return result["paths"][path][method]["responses"]["400"]["content"][
        "application/json"
    ]["schema"]


@pytest.fixture
def validator():
    """Validates a body against the schema published for a 400."""
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "components": {"schemas": ERROR_SCHEMAS},
    }
    registry = Registry().with_resource(
        DOCUMENT_URI, Resource.from_contents(document)
    )
    schema = {
        "anyOf": [
            {"$ref": DOCUMENT_URI + ref["$ref"]}
            for ref in VALIDATION_OR_PLAIN_ERROR["anyOf"]
        ]
    }
    return Draft202012Validator(schema, registry=registry)


def test_a_generic_400_becomes_the_validation_or_plain_error():
    result = {"paths": {"/a": {"post": _operation(copy.deepcopy(GENERIC))}}}

    publish_error_schemas(result, None, None, True)

    assert _documented_400(result, "/a", "post") == VALIDATION_OR_PLAIN_ERROR
    assert {"ApiValidationError", "ApiError"} <= set(
        result["components"]["schemas"]
    )


def test_a_400_a_view_names_itself_is_left_alone():
    result = {"paths": {"/a": {"get": _operation(copy.deepcopy(SPECIFIC))}}}

    publish_error_schemas(result, None, None, True)

    assert _documented_400(result, "/a", "get") == SPECIFIC


@pytest.mark.django_db
def test_a_validation_400_has_fields_and_no_detail(validator):
    response = APIClient().post(reverse("contact"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "fields" in response.json()
    assert "detail" not in response.json()
    validator.validate(response.json())


@pytest.mark.django_db
def test_a_malformed_body_400_is_the_plain_error(validator):
    response = APIClient().post(
        reverse("contact"), "{bad", content_type="application/json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.json()) == {"detail", "code"}
    validator.validate(response.json())


def test_no_generated_operation_documents_a_400_as_the_generic_error():
    schema = SchemaGenerator().get_schema(request=None, public=True)

    offenders = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
        for media in operation.get("responses", {})
        .get("400", {})
        .get("content", {})
        .values()
        if media.get("schema") == GENERIC
    ]

    assert offenders == []
