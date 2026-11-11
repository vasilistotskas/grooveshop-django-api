"""Where allauth's headless OpenAPI document is not what allauth serves.

The document is written by hand upstream. ``manage.py allauth_schema`` writes
it adapted to this project, then applies these corrections, each a place the
document and the code (allauth's, or ours) disagree. A correction names the
source it was read from, and ``tests/integration/user/
test_allauth_response_contract.py`` drives the real endpoint, checks every
key it serves is documented, and so fails without the correction (the
provider ids of a ``provider_token`` flow need a configured social app and
are read from the source only). Prefer a hook allauth offers (``TenantHeadlessAdapter``) to an
entry here; an entry whose upstream document is fixed becomes a no-op.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from allauth.headless.constants import Flow

Document = dict[str, Any]

OUR_PATH_PREFIX = "/_allauth/app/v1"


def _schemas(document: Document) -> dict[str, Any]:
    return document["components"]["schemas"]


def declare_every_flow(document: Document) -> None:
    """``Flow.id`` lists every ``allauth.headless.constants.Flow``.

    The enum omits ``mfa_login_webauthn``, ``mfa_signup_webauthn``,
    ``mfa_trust`` and ``password_reset_by_code``, which responses carry.
    """
    schema = _schemas(document)["Flow"]["properties"]["id"]
    schema["enum"] = sorted({*schema["enum"], *(flow.value for flow in Flow)})


def declare_flow_providers(document: Document) -> None:
    """``Flow.providers``: the ids a ``provider_redirect`` or
    ``provider_token`` flow offers (``headless/socialaccount/response.py``,
    ``provider_flows``). The document only has ``provider``, the object of a
    ``provider_signup`` flow."""
    _schemas(document)["Flow"]["properties"]["providers"] = {
        "type": "array",
        "description": "The ids of the providers the flow offers.",
        "items": {"type": "string"},
    }


def make_is_authenticated_nullable(document: Document) -> None:
    """``meta.is_authenticated`` is ``None`` when there is no user at all
    (an expired or unknown session): ``headless/base/response.py`` sets it to
    ``user and user.is_authenticated``."""
    for part in _schemas(document)["AuthenticationMeta"]["allOf"]:
        properties = part.get("properties", {})
        if "is_authenticated" in properties:
            properties["is_authenticated"]["nullable"] = True


def make_optional_timestamp_nullable(document: Document) -> None:
    """``OptionalTimestamp`` is ``null`` for an authenticator never used
    (``headless/mfa/response.py``: ``last_used_at`` is ``None``). The
    document writes ``nullable: true`` next to a ``$ref``, where OpenAPI 3.0
    ignores it, so the schema in effect is a plain ``Timestamp``."""
    timestamp = _schemas(document)["Timestamp"]
    _schemas(document)["OptionalTimestamp"] = {
        **{key: copy.deepcopy(value) for key, value in timestamp.items()},
        "nullable": True,
    }


def declare_passkey_login_enabled(document: Document) -> None:
    """``mfa.passkey_login_enabled`` is always served
    (``headless/mfa/response.py``)."""
    schema = _schemas(document)["MFAConfiguration"]
    schema["properties"]["passkey_login_enabled"] = {
        "type": "boolean",
        "description": "Matches `settings.MFA_PASSKEY_LOGIN_ENABLED`.",
    }
    schema["required"] = sorted({*schema["required"], "passkey_login_enabled"})


def declare_authentication_method(document: Document) -> None:
    """``account.authentication_method`` is served for backwards
    compatibility (``headless/base/response.py``, ``get_config_data``). The
    document lists it as required but never defines it."""
    schema = _schemas(document)["AccountConfiguration"]
    schema["properties"]["authentication_method"] = {
        "type": "string",
        "enum": ["email", "username", "username_email"],
        "deprecated": True,
        "description": "Derived from `login_methods`.",
    }


def declare_recovery_codes_regeneration_answer(document: Document) -> None:
    """``POST .../recovery-codes`` answers 200 with the codes, as ``GET``
    does (``headless/mfa/views.py``, ``ManageRecoveryCodesView.post``)."""
    path = document["paths"][
        f"{OUR_PATH_PREFIX}/account/authenticators/recovery-codes"
    ]
    path["post"]["responses"] = {
        "200": copy.deepcopy(path["get"]["responses"]["200"]),
        **path["post"]["responses"],
    }


def declare_passkey_signup_creation_options(document: Document) -> None:
    """``GET .../auth/webauthn/signup`` answers creation options: a signup
    registers a credential (``headless/mfa/views.py``,
    ``SignupWebAuthnView.get``, returns ``AddWebAuthnResponse``). The
    document declares the request options of a sign-in."""
    operation = document["paths"][f"{OUR_PATH_PREFIX}/auth/webauthn/signup"][
        "get"
    ]
    operation["description"] = (
        "Returns the WebAuthn credential creation options, that can be "
        "processed using `parseCreationOptionsFromJSON()` on the frontend.\n"
    )
    operation["responses"]["200"] = {
        "$ref": "#/components/responses/WebAuthnCreationOptionsResponse"
    }


def declare_totp_svg(document: Document) -> None:
    """``GET .../totp/svg`` is ours (``core.views.ManageTOTPSvgView``): the
    TOTP status endpoint plus the QR code as an SVG, for a user without an
    authenticator. Its 404 carries the secret to enrol."""
    schemas = _schemas(document)
    schemas["TOTPSvgNotFoundResponse"] = {
        "type": "object",
        "description": (
            "No TOTP authenticator yet: the secret to enrol and its QR code."
        ),
        "properties": {
            "status": {"type": "integer", "enum": [404]},
            "meta": {
                "type": "object",
                "properties": {
                    "secret": {"type": "string"},
                    "totp_url": {"type": "string"},
                    "totp_svg": {
                        "type": "string",
                        "description": "The QR code, as SVG markup.",
                    },
                },
                "required": [
                    "secret",
                    "totp_url",
                    "totp_svg",
                ],
            },
        },
        "required": ["status", "meta"],
    }
    status = document["paths"][
        f"{OUR_PATH_PREFIX}/account/authenticators/totp"
    ]["get"]["responses"]["200"]
    document["paths"][f"{OUR_PATH_PREFIX}/account/authenticators/totp/svg"] = {
        "get": {
            "tags": ["Account: 2FA"],
            "summary": "Get the TOTP authenticator, or a secret to enrol",
            "description": (
                "Like the TOTP status endpoint, but the 404 for a user "
                "without an authenticator carries the secret, its otpauth "
                "URL and the QR code."
            ),
            "parameters": [{"$ref": "#/components/parameters/SessionToken"}],
            "responses": {
                "200": copy.deepcopy(status),
                "404": {
                    "description": "No authenticator: the secret to enrol.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/TOTPSvgNotFoundResponse"
                            }
                        }
                    },
                },
            },
        }
    }


def name_totp_not_found_response(document: Document) -> None:
    """The TOTP status 404 is a named schema, so a client generates a type
    for it. The document declares its body inline, in the response."""
    response = document["components"]["responses"]["TOTPAuthenticatorNotFound"]
    content = response["content"]["application/json"]
    _schemas(document)["TOTPNotFoundResponse"] = content["schema"]
    content["schema"] = {"$ref": "#/components/schemas/TOTPNotFoundResponse"}


def declare_rate_limit_answer(document: Document) -> None:
    """Any operation can answer 429: ``RateLimitResponse``
    (``headless/base/response.py``) is what a rate-limited call gets, and
    the limits are ours to configure (``ACCOUNT_RATE_LIMITS``). The document
    declares no 429."""
    _schemas(document)["RateLimitResponse"] = {
        "type": "object",
        "description": "Too many requests: the call is rate limited.",
        "properties": {"status": {"type": "integer", "enum": [429]}},
        "required": ["status"],
    }
    answer = {
        "description": "Too many requests.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/RateLimitResponse"}
            }
        },
    }
    for path in document["paths"].values():
        for operation in path.values():
            if isinstance(operation, dict) and "responses" in operation:
                operation["responses"].setdefault("429", copy.deepcopy(answer))


def _declares(document: Document, schema: dict[str, Any], key: str) -> bool:
    """Whether ``schema`` declares property ``key``, through ``$ref``,
    ``allOf`` and ``oneOf``."""
    while "$ref" in schema:
        target: Any = document
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        schema = target
    if key in schema.get("properties", {}):
        return True
    return any(
        _declares(document, part, key)
        for name in ("allOf", "oneOf", "anyOf")
        for part in schema.get(name, [])
    )


def _add_session_meta(document: Document, schema: dict[str, Any]) -> None:
    if "$ref" in schema:
        target: Any = document
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return _add_session_meta(document, target)
    if not _declares(document, schema, "status") or _declares(
        document, schema, "meta"
    ):
        return None
    meta = {"$ref": "#/components/schemas/BaseAuthenticationMeta"}
    if "properties" in schema:
        schema["properties"]["meta"] = meta
    elif "allOf" in schema:
        schema["allOf"].append({"type": "object", "properties": {"meta": meta}})
    else:
        for part in schema.get("oneOf", []):
            _add_session_meta(document, part)
    return None


def allow_session_meta_on_every_answer(document: Document) -> None:
    """Every answer to an app client may carry ``meta.session_token`` (and
    the access token): ``APIResponse._add_session_meta`` adds it to whatever
    is being answered when the token is exposed, rotated or issued. The
    document declares ``meta`` only on the authentication answers, so a
    client parsing ``GET /auth/webauthn/login`` or a resend would drop the
    token it must send next. Optional ``meta`` on each answer that has a
    ``status`` and no ``meta`` of its own."""
    holders = list(document["components"].get("responses", {}).values())
    for path in document["paths"].values():
        for operation in path.values():
            if isinstance(operation, dict) and "responses" in operation:
                holders += [
                    response
                    for response in operation["responses"].values()
                    if "$ref" not in response
                ]
    for holder in holders:
        content = holder.get("content", {}).get("application/json")
        if content:
            _add_session_meta(document, content["schema"])


CORRECTIONS: tuple[Callable[[Document], None], ...] = (
    declare_every_flow,
    declare_flow_providers,
    make_is_authenticated_nullable,
    make_optional_timestamp_nullable,
    declare_passkey_login_enabled,
    declare_authentication_method,
    declare_recovery_codes_regeneration_answer,
    declare_passkey_signup_creation_options,
    declare_totp_svg,
    name_totp_not_found_response,
    declare_rate_limit_answer,
    allow_session_meta_on_every_answer,
)


def apply_corrections(document: Document) -> None:
    for correct in CORRECTIONS:
        correct(document)
