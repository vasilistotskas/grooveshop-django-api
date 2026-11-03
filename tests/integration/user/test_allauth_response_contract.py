"""Our real headless endpoints answer what ``allauth-schema.yml`` says.

The storefront generates its all-auth parsers from that file, which comes
from allauth's hand-maintained specification (``manage.py allauth_schema``).
The specification cannot know our ``TenantHeadlessAdapter``,
``SessionTokenStrategy``, signup form or settings, so each flow the
storefront depends on is driven through the real endpoint here and its
body is judged against the generated document. Where they disagree, ours
wins and the disagreement is a failure to fix, not a schema to loosen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from allauth.account.models import EmailAddress
from django.test import Client
from rest_framework import status

from tests.utils.openapi import as_json_schema
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

BASE = "/_allauth/app/v1"
PASSWORD = "S3cure-pass-phrase!"
DOCUMENT = Path(__file__).resolve().parents[3] / "allauth-schema.yml"


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return yaml.safe_load(DOCUMENT.read_text(encoding="utf-8"))


def _resolve(document: dict, node: dict) -> dict:
    while "$ref" in node:
        target: Any = document
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def assert_documented(document: dict, path: str, method: str, response) -> dict:
    """``response``'s status is documented for the operation and its JSON
    body satisfies the schema declared for it."""
    operation = document["paths"][f"{BASE}{path}"][method]
    declared = operation["responses"]
    assert str(response.status_code) in declared, (
        f"{method.upper()} {path} answered {response.status_code}, which "
        f"the specification does not document: {sorted(declared)}"
    )
    content = _resolve(document, declared[str(response.status_code)]).get(
        "content", {}
    )
    schema = content["application/json"]["schema"]
    body = response.json()
    jsonschema.Draft4Validator(
        as_json_schema(schema, document["components"]["schemas"])
    ).validate(body)
    return body


def _call(client: Client, method: str, path: str, body=None, token=None):
    headers = {"HTTP_X_SESSION_TOKEN": token} if token else {}
    return getattr(client, method)(
        f"{BASE}{path}",
        data=json.dumps(body) if body is not None else None,
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def user():
    account = UserAccountFactory(plain_password=PASSWORD)
    EmailAddress.objects.create(
        user=account, email=account.email, verified=True, primary=True
    )
    return account


@pytest.fixture
def token(user):
    response = _call(
        Client(),
        "post",
        "/auth/login",
        {"email": user.email, "password": PASSWORD},
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()["meta"]["session_token"]


def test_the_document_covers_the_flows_the_storefront_uses(document):
    paths = {path.removeprefix(BASE) for path in document["paths"]}

    assert {
        "/config",
        "/auth/login",
        "/auth/signup",
        "/auth/session",
        "/auth/2fa/authenticate",
        "/auth/code/request",
        "/account/authenticators",
    } <= paths


def test_the_configuration(document):
    response = _call(Client(), "get", "/config")

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/config", "get", response)


def test_an_anonymous_session(document):
    response = _call(Client(), "get", "/auth/session")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert_documented(document, "/auth/session", "get", response)


def test_a_login_with_a_wrong_password(document, user):
    response = _call(
        Client(),
        "post",
        "/auth/login",
        {"email": user.email, "password": "wrong-password-1!"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert_documented(document, "/auth/login", "post", response)


def test_a_login(document, user):
    response = _call(
        Client(),
        "post",
        "/auth/login",
        {"email": user.email, "password": PASSWORD},
    )

    assert response.status_code == status.HTTP_200_OK
    body = assert_documented(document, "/auth/login", "post", response)
    assert body["data"]["user"]["email"] == user.email


def test_a_session_once_logged_in(document, token):
    response = _call(Client(), "get", "/auth/session", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/auth/session", "get", response)


def test_a_signup_that_awaits_email_verification(document):
    response = _call(
        Client(),
        "post",
        "/auth/signup",
        {
            "email": "new.customer@example.com",
            "password": PASSWORD,
        },
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert_documented(document, "/auth/signup", "post", response)


def test_a_signup_the_form_refuses(document):
    response = _call(
        Client(),
        "post",
        "/auth/signup",
        {"email": "not-an-email", "password": "x"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert_documented(document, "/auth/signup", "post", response)


def test_a_login_code_request(document, user):
    response = _call(
        Client(), "post", "/auth/code/request", {"email": user.email}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert_documented(document, "/auth/code/request", "post", response)


def test_the_authenticators_list(document, token):
    response = _call(Client(), "get", "/account/authenticators", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/account/authenticators", "get", response)


def test_the_email_addresses(document, token):
    response = _call(Client(), "get", "/account/email", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/account/email", "get", response)


def test_the_connected_providers(document, token):
    response = _call(Client(), "get", "/account/providers", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/account/providers", "get", response)


def test_the_logged_in_sessions(document, token):
    response = _call(Client(), "get", "/auth/sessions", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/auth/sessions", "get", response)


def test_a_login_that_awaits_the_second_factor(document, user):
    from allauth.mfa.models import Authenticator
    from allauth.mfa.totp.internal import auth as totp_auth

    secret = totp_auth.generate_totp_secret()
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.TOTP,
        data={"secret": secret},
    )

    response = _call(
        Client(),
        "post",
        "/auth/login",
        {"email": user.email, "password": PASSWORD},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    body = assert_documented(document, "/auth/login", "post", response)
    assert any(
        flow["id"] == "mfa_authenticate" and flow.get("is_pending")
        for flow in body["data"]["flows"]
    )

    wrong = _call(
        Client(),
        "post",
        "/auth/2fa/authenticate",
        {"code": "000000"},
        token=body["meta"]["session_token"],
    )
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    assert_documented(document, "/auth/2fa/authenticate", "post", wrong)
