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
    # A schema that does not forbid extra keys accepts them, so a field the
    # endpoint serves and the document omits would pass the check above and
    # still be missing from the generated parser.
    assert not undocumented(document, schema, body), undocumented(
        document, schema, body
    )
    return body


def _properties(document: dict, schema: dict) -> dict:
    """Every property a schema declares, through ``$ref``, ``allOf`` and the
    branches of ``oneOf``/``anyOf``."""
    schema = _resolve(document, schema)
    properties = dict(schema.get("properties", {}))
    for key in ("allOf", "oneOf", "anyOf"):
        for part in schema.get(key, []):
            for name, value in _properties(document, part).items():
                properties.setdefault(name, value)
    return properties


def undocumented(
    document: dict, schema: dict, body: Any, where: str = ""
) -> list[str]:
    """The keys of ``body`` that ``schema`` does not declare, by path."""
    schema = _resolve(document, schema)
    if isinstance(body, list) and "items" in schema:
        return [
            key
            for item in body
            for key in undocumented(document, schema["items"], item, where)
        ]
    if not isinstance(body, dict):
        return []
    properties = _properties(document, schema)
    if not properties or schema.get("additionalProperties"):
        return []
    found = []
    for key, value in body.items():
        if key not in properties:
            found.append(f"{where}{key}")
        else:
            found += undocumented(
                document, properties[key], value, f"{where}{key}."
            )
    return found


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


def test_an_expired_session_is_gone_and_unauthenticated_is_null(document):
    response = _call(
        Client(), "get", "/auth/session", token="not-a-session-token"
    )

    assert response.status_code == status.HTTP_410_GONE
    body = assert_documented(document, "/auth/session", "get", response)
    assert body["meta"]["is_authenticated"] is None


def test_the_configuration_documents_every_key_it_serves(document):
    response = _call(Client(), "get", "/config")

    body = assert_documented(document, "/config", "get", response)
    assert "passkey_login_enabled" in body["data"]["mfa"]
    assert body["data"]["account"]["authentication_method"] == "email"


def test_regenerating_recovery_codes(document, user, token):
    from allauth.mfa.models import Authenticator
    from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
    from allauth.mfa.totp.internal import auth as totp_auth

    # Codes are regenerated for a user who has another authenticator.
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.TOTP,
        data={"secret": totp_auth.generate_totp_secret()},
    )
    RecoveryCodes.activate(user)
    # Regenerating is a sensitive action: it asks for the password again.
    reauthenticated = _call(
        Client(),
        "post",
        "/auth/reauthenticate",
        {"password": PASSWORD},
        token=token,
    )
    assert reauthenticated.status_code == status.HTTP_200_OK

    response = _call(
        Client(), "post", "/account/authenticators/recovery-codes", token=token
    )

    assert response.status_code == status.HTTP_200_OK
    assert_documented(
        document, "/account/authenticators/recovery-codes", "post", response
    )


def test_the_totp_enrolment_with_its_qr_code(document, token):
    response = _call(
        Client(), "get", "/account/authenticators/totp/svg", token=token
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = assert_documented(
        document, "/account/authenticators/totp/svg", "get", response
    )
    assert body["meta"]["totp_svg"].lstrip().startswith("<")


def test_the_totp_status_without_an_authenticator(document, token):
    response = _call(
        Client(), "get", "/account/authenticators/totp", token=token
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = assert_documented(
        document, "/account/authenticators/totp", "get", response
    )
    assert set(body["meta"]) >= {"secret", "totp_url"}


def test_a_rate_limited_call(document, user, settings):
    settings.ACCOUNT_RATE_LIMITS = {"login_failed": False, "login": "1/m/ip"}
    client = Client()
    body = {"email": user.email, "password": PASSWORD}

    first = _call(client, "post", "/auth/login", body)
    second = _call(client, "post", "/auth/login", body)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert_documented(document, "/auth/login", "post", second)


def test_the_passkey_login_options_carry_the_session_token(document):
    response = _call(Client(), "get", "/auth/webauthn/login")

    assert response.status_code == status.HTTP_200_OK
    body = assert_documented(document, "/auth/webauthn/login", "get", response)
    assert body["meta"]["session_token"]


def test_the_passkey_signup_options_are_creation_options(document, settings):
    """A passkey signup registers a new credential, so the browser needs
    creation options, whatever the upstream document calls them."""
    # Verifying the email is the stage before this one.
    settings.ACCOUNT_EMAIL_VERIFICATION = "none"
    pending = _call(
        Client(),
        "post",
        "/auth/webauthn/signup",
        {"email": "passkey@example.com"},
    )
    assert pending.status_code == status.HTTP_401_UNAUTHORIZED
    token = pending.json()["meta"]["session_token"]

    response = _call(Client(), "get", "/auth/webauthn/signup", token=token)

    assert response.status_code == status.HTTP_200_OK
    body = assert_documented(document, "/auth/webauthn/signup", "get", response)
    assert body["data"]["creation_options"]["publicKey"]


def test_resending_a_login_code_carries_the_session_token(document, user):
    pending = _call(
        Client(), "post", "/auth/code/request", {"email": user.email}
    )
    token = pending.json()["meta"]["session_token"]

    response = _call(Client(), "post", "/auth/code/resend", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/auth/code/resend", "post", response)


def test_resending_an_email_verification_carries_the_session_token(
    document, settings
):
    # The signup just sent one, and a second inside the window is a 429.
    settings.ACCOUNT_RATE_LIMITS = {"confirm_email": False}
    signup = _call(
        Client(),
        "post",
        "/auth/signup",
        {"email": "pending.customer@example.com", "password": PASSWORD},
    )
    token = signup.json()["meta"]["session_token"]

    response = _call(Client(), "post", "/auth/email/verify/resend", token=token)

    assert response.status_code == status.HTTP_200_OK
    assert_documented(document, "/auth/email/verify/resend", "post", response)
