"""The schema documents each error body the handler really sends."""

import copy
from uuid import uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.api.schema import PLAIN_400_BODIES, publish_error_schemas
from core.api.throttling import ProductAlertCreateThrottle
from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from product.factories.product import ProductFactory
from product.models.alert import ProductAlertKind
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory

GENERIC = {"$ref": "#/components/schemas/ErrorResponse"}
SPECIFIC = {"$ref": "#/components/schemas/B2BErrorResponse"}


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


def test_a_generic_400_becomes_the_validation_or_plain_error():
    result = {"paths": {"/a": {"post": _operation(copy.deepcopy(GENERIC))}}}

    publish_error_schemas(result, None, None, True)

    assert _documented_400(result, "/a", "post") == {
        "anyOf": [
            {"$ref": "#/components/schemas/ApiValidationError"},
            {"$ref": "#/components/schemas/ApiError"},
        ]
    }
    assert {"ApiValidationError", "ApiError"} <= set(
        result["components"]["schemas"]
    )


def test_a_400_a_view_names_itself_is_left_alone():
    result = {"paths": {"/a": {"get": _operation(copy.deepcopy(SPECIFIC))}}}

    publish_error_schemas(result, None, None, True)

    assert _documented_400(result, "/a", "get") == SPECIFIC


@pytest.mark.django_db
def test_a_validation_400_has_fields_and_no_detail(documented):
    response = APIClient().post(reverse("contact"), {}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "fields" in response.json()
    assert "detail" not in response.json()
    documented(response, "/api/v1/contact", "post")


@pytest.mark.django_db
def test_a_malformed_body_400_is_the_plain_error(documented):
    response = APIClient().post(
        reverse("contact"), "{bad", content_type="application/json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/contact", "post")


@pytest.mark.django_db
def test_a_401_is_the_documented_plain_error(documented):
    product = ProductFactory()

    response = APIClient().delete(reverse("product-detail", args=[product.pk]))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/product/{id}", "delete")


@pytest.mark.django_db
def test_a_403_is_the_documented_plain_error(documented):
    product = ProductFactory()
    client = APIClient()
    client.force_authenticate(UserAccountFactory(num_addresses=0))

    response = client.delete(reverse("product-detail", args=[product.pk]))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/product/{id}", "delete")


@pytest.mark.django_db
def test_a_404_is_the_documented_plain_error(documented):
    response = APIClient().get(reverse("product-detail", args=[0]))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/product/{id}", "get")


@pytest.mark.django_db
@pytest.mark.usefixtures("open_email_gates")
def test_a_409_is_the_documented_plain_error(documented):
    product = ProductFactory(active=True)
    user = UserAccountFactory(num_addresses=0)
    optout.opt_out(
        user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        reverse("product-alert-list"),
        {"kind": ProductAlertKind.RESTOCK, "product": product.pk},
        format="json",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/product/alert", "post")


@pytest.mark.django_db
@pytest.mark.usefixtures("open_email_gates")
def test_a_429_is_the_documented_plain_error(documented, monkeypatch):
    monkeypatch.setattr(
        ProductAlertCreateThrottle, "rate", "1/hour", raising=False
    )
    monkeypatch.setattr(
        ProductAlertCreateThrottle,
        "cache",
        LocMemCache(f"alert-{uuid4()}", {}),
        raising=False,
    )
    product = ProductFactory(active=True)
    body = {
        "kind": ProductAlertKind.RESTOCK,
        "product": product.pk,
        "email": "guest-throttle@example.com",
    }
    client = APIClient()
    url = reverse("product-alert-list")

    client.post(url, body, format="json")
    response = client.post(url, body, format="json")

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert set(response.json()) == {"detail", "code"}
    documented(response, "/api/v1/product/alert", "post")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "content_type", "expected"),
    [
        ("delete", None, status.HTTP_405_METHOD_NOT_ALLOWED),
        ("post", "text/plain", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    ],
    ids=["405", "415"],
)
def test_an_undocumented_status_is_still_the_plain_error(
    method, content_type, expected
):
    """No operation documents a 405 or 415; the body is the same one."""
    client = APIClient()
    kwargs = {"content_type": content_type} if content_type else {}

    response = getattr(client, method)(
        reverse("contact"), "x" if content_type else None, **kwargs
    )

    assert response.status_code == expected
    assert set(response.json()) == {"detail", "code"}


def test_no_generated_operation_documents_a_plain_400_alone(
    generated_schema,
):
    offenders = [
        f"{method.upper()} {path}"
        for path, operations in generated_schema["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
        for media in operation.get("responses", {})
        .get("400", {})
        .get("content", {})
        .values()
        if media.get("schema", {}).get("$ref", "").rsplit("/", 1)[-1]
        in PLAIN_400_BODIES
    ]

    assert offenders == []
