"""Store-admin tools over MCP, through the real schemas and hosts.

The staff issuer keeps its clients and tokens in the PUBLIC schema and a
store's own issuer keeps its customers' in the store's schema
(``agent/staff.py``). The main suite cannot see that difference: it runs
every app's tables in ``public``. So the claims that matter most are
proven here:

- a customer token from the store's own issuer never opens a staff tool,
  even one bound to the staff resource;
- a staff token works only on the store it was connected to, only while
  the holder is on that store's team, and never on the shopper surface;
- the connect host serves the staff issuer and nothing else, and the
  platform host no longer serves an issuer at all;
- the whole flow, from registering a client to reading an order, works
  end to end.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.test import Client as HttpClient
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django_tenants.test.client import TenantClient
from django_tenants.utils import (
    get_public_schema_name,
    schema_context,
    tenant_context,
)

pytestmark = pytest.mark.django_db

CONNECT_HOST = "connect.test"
PASSWORD = secrets.token_urlsafe(16)
REDIRECT_URI = "https://agent.example/callback"

STAFF_ENDPOINTS = (
    "agent-staff-me",
    "agent-staff-orders",
    "agent-staff-sales",
    "agent-staff-products",
    "agent-staff-customers",
    "agent-staff-reviews",
    "agent-staff-messages",
)


def _allow(tenant, on: bool = True) -> None:
    """The store's own switch for its team's AI apps (off by default)."""
    from tests.utils.store_settings import switch_store_settings

    with tenant_context(tenant):
        switch_store_settings(STAFF_AI_ACCESS_ENABLED=on)


@pytest.fixture(autouse=True)
def _stores_allow_staff_ai_access(request):
    for name in ("mt_tenant", "mt_tenant_b"):
        if name in request.fixturenames:
            _allow(request.getfixturevalue(name))


def _resource(tenant) -> str:
    return f"https://{tenant.get_primary_domain().domain}/mcp/admin"


def _staff_member(email: str, tenant, role: str):
    from tenant.models import UserTenantMembership
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user = UserAccount.objects.create_user(
            email=email, password=PASSWORD, is_staff=True
        )
        UserTenantMembership.objects.create(
            user=user, tenant=tenant, role=role, is_active=True
        )
    return user


def _join_team(email: str, tenant, role: str = "staff") -> None:
    """Put an existing staff member on another store's team too."""
    from tenant.models import UserTenantMembership
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        UserTenantMembership.objects.create(
            user=UserAccount.objects.get(email=email),
            tenant=tenant,
            role=role,
            is_active=True,
        )


def _mint(user, *, resource: str | None, scopes: list[str]) -> str:
    """An access token in the schema the connection is bound to."""
    from allauth.idp.oidc.models import Client, Token

    client = Client.objects.create(name="Staff test agent")
    client.set_scopes(scopes)
    client.save()
    raw = secrets.token_urlsafe(32)
    token = Token(
        type=Token.Type.ACCESS_TOKEN,
        client=client,
        user=user,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    token.set_value(raw)
    token.set_scopes(scopes)
    if resource:
        token.set_resources([resource])
    token.save()
    return raw


def _staff_token(user, tenant, scopes: list[str]) -> str:
    with schema_context(get_public_schema_name()):
        return _mint(user, resource=_resource(tenant), scopes=scopes)


def _get(tenant, name: str, token: str):
    return TenantClient(tenant).get(
        reverse(name), HTTP_AUTHORIZATION=f"Bearer {token}"
    )


ALL_READ = [
    "admin:orders:read",
    "admin:sales:read",
    "admin:profit:read",
    "admin:catalog:read",
    "admin:customers:read",
    "admin:reviews:read",
    "admin:messages:read",
]


def test_a_staff_token_from_public_reads_its_store(mt_tenant):
    owner = _staff_member("mt-staff-owner@example.com", mt_tenant, "owner")
    token = _staff_token(owner, mt_tenant, ALL_READ)

    for name in STAFF_ENDPOINTS:
        response = _get(mt_tenant, name, token)
        assert response.status_code == 200, (name, response.content)

    me = _get(mt_tenant, "agent-staff-me", token).json()
    assert me["role"] == "owner"
    assert me["storeDomain"] == mt_tenant.get_primary_domain().domain


def test_a_customer_token_never_opens_a_staff_tool(mt_tenant):
    """Even bound to the staff resource and holding every staff scope: it
    sits in the store's schema, where staff tokens are never looked up."""
    from user.models import UserAccount

    with tenant_context(mt_tenant):
        customer = UserAccount.objects.create_user(
            email="mt-staff-customer@example.com", password=PASSWORD
        )
        token = _mint(customer, resource=_resource(mt_tenant), scopes=ALL_READ)

    for name in STAFF_ENDPOINTS:
        response = _get(mt_tenant, name, token)
        assert response.status_code == 401, (name, response.content)
        assert response["WWW-Authenticate"] == 'Bearer realm="agent-staff"'


def test_a_staff_token_never_opens_the_shopper_surface(mt_tenant):
    owner = _staff_member("mt-staff-shopper@example.com", mt_tenant, "owner")
    token = _staff_token(owner, mt_tenant, [*ALL_READ, "profile"])

    response = _get(mt_tenant, "agent-me", token)

    assert response.status_code == 401


def test_a_token_connected_to_another_store_is_refused(mt_tenant, mt_tenant_b):
    """Staff of both stores, token connected to B, presented at A."""
    from tenant.models import UserTenantMembership

    member = _staff_member("mt-staff-two@example.com", mt_tenant_b, "owner")
    with schema_context(get_public_schema_name()):
        UserTenantMembership.objects.create(
            user=member, tenant=mt_tenant, role="owner", is_active=True
        )
    token = _staff_token(member, mt_tenant_b, ALL_READ)

    assert _get(mt_tenant, "agent-staff-orders", token).status_code == 401
    assert _get(mt_tenant_b, "agent-staff-orders", token).status_code == 200


def test_staff_of_another_store_is_refused(mt_tenant, mt_tenant_b):
    """Staff of B only, holding a token bound to A's resource."""
    outsider = _staff_member("mt-staff-b@example.com", mt_tenant_b, "owner")
    token = _staff_token(outsider, mt_tenant, ALL_READ)

    for name in STAFF_ENDPOINTS:
        assert _get(mt_tenant, name, token).status_code == 401, name


def test_leaving_the_team_ends_access_at_the_next_call(mt_tenant):
    from tenant.models import UserTenantMembership

    staff = _staff_member("mt-staff-leaver@example.com", mt_tenant, "staff")
    token = _staff_token(staff, mt_tenant, ALL_READ)
    assert _get(mt_tenant, "agent-staff-orders", token).status_code == 200

    with schema_context(get_public_schema_name()):
        UserTenantMembership.objects.filter(user=staff).update(is_active=False)

    assert _get(mt_tenant, "agent-staff-orders", token).status_code == 401


# ── the connect host ────────────────────────────────────────────────


def _connect_client() -> HttpClient:
    return HttpClient(HTTP_HOST=CONNECT_HOST)


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_the_connect_host_serves_the_staff_issuer_and_nothing_else(
    mt_public_tenant,
):
    client = _connect_client()

    metadata = client.get(
        "/.well-known/oauth-authorization-server", secure=True
    )
    assert metadata.status_code == 200
    document = metadata.json()
    assert document["issuer"] == f"https://{CONNECT_HOST}"
    assert "admin:orders:read" in document["scopes_supported"]
    assert "orders:read" not in document["scopes_supported"]
    assert "device_authorization_endpoint" not in document

    assert client.get("/identity/staff/login/", secure=True).status_code == 200
    for path in ("/admin/", "/accounts/login/", "/identity/o/device"):
        assert client.get(path, secure=True).status_code == 404, path


def test_the_platform_host_serves_no_issuer(mt_public_tenant):
    from tenant.models import TenantDomain

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.create(
            domain="platform.test", tenant=mt_public_tenant, is_primary=True
        )
    client = HttpClient(HTTP_HOST="platform.test")

    for path in (
        "/identity/o/authorize",
        "/.well-known/openid-configuration",
        "/.well-known/oauth-authorization-server",
    ):
        assert client.get(path, secure=True).status_code == 404, path


def _register(client: HttpClient) -> str:
    response = client.post(
        "/identity/o/api/clients",
        data=json.dumps(
            {
                "client_name": "MT staff agent",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": " ".join([*ALL_READ, "offline_access"]),
            }
        ),
        content_type="application/json",
        secure=True,
    )
    assert response.status_code == 201, response.content
    return response.json()["client_id"]


def _staff_client() -> str:
    """A public staff client as DCR leaves it, made directly: allauth limits
    registration per address, and only the end-to-end test needs to prove
    DCR itself."""
    from allauth.idp.oidc.models import Client

    with schema_context(get_public_schema_name()):
        client = Client(name="MT staff app", type=Client.Type.PUBLIC)
        client.set_redirect_uris([REDIRECT_URI])
        client.set_grant_types(["authorization_code", "refresh_token"])
        client.set_response_types(["code"])
        client.set_scopes([*ALL_READ, "offline_access"])
        client.save()
    return client.id


def _authorize_query(client_id: str, resource: str, challenge: str) -> str:
    return urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join([*ALL_READ, "offline_access"]),
            "resource": resource,
            "state": "s1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _sign_in(client: HttpClient, email: str, next_url: str):
    return client.post(
        f"/identity/staff/login/?{urlencode({'next': next_url})}",
        {
            "step": "password",
            "username": email,
            "password": PASSWORD,
            "next": next_url,
        },
        secure=True,
    )


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_staff_member_connects_an_ai_app_end_to_end(
    mt_public_tenant, mt_tenant
):
    staff = _staff_member("mt-staff-flow@example.com", mt_tenant, "staff")
    client = _connect_client()
    client_id = _register(client)
    verifier, challenge = _pkce()
    authorize = "/identity/o/authorize?" + _authorize_query(
        client_id, _resource(mt_tenant), challenge
    )

    # Not signed in: sent to the staff sign-in, never allauth's.
    first = client.get(authorize, secure=True)
    assert first.status_code == 302
    assert urlsplit(first["Location"]).path == "/identity/staff/login/"

    signed_in = _sign_in(client, staff.email, authorize)
    assert signed_in.status_code == 302, signed_in.content
    assert signed_in["Location"] == authorize

    consent = client.get(authorize, secure=True)
    assert consent.status_code == 200
    offered = consent.context["form"].fields["scopes"].choices
    offered_scopes = {scope for scope, _label in offered}
    # A STAFF role is never offered the takings or the profit.
    assert "admin:orders:read" in offered_scopes
    assert "admin:sales:read" not in offered_scopes
    assert "admin:profit:read" not in offered_scopes

    granted = client.post(
        "/identity/o/authorize",
        {
            "request": consent.context["form"].initial["request"],
            "scopes": sorted(offered_scopes),
            "action": "grant",
        },
        secure=True,
    )
    assert granted.status_code == 302, granted.content
    callback = parse_qs(urlsplit(granted["Location"]).query)
    assert callback["state"] == ["s1"]

    token = client.post(
        "/identity/o/api/token",
        {
            "grant_type": "authorization_code",
            "code": callback["code"][0],
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": _resource(mt_tenant),
        },
        secure=True,
    )
    assert token.status_code == 200, token.content
    access_token = token.json()["access_token"]

    orders = _get(mt_tenant, "agent-staff-orders", access_token)
    assert orders.status_code == 200, orders.content
    # The scope was never offered, so the token cannot hold it.
    assert _get(mt_tenant, "agent-staff-sales", access_token).status_code == 403


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_someone_off_the_team_is_refused_at_consent(
    mt_public_tenant, mt_tenant, mt_tenant_b
):
    outsider = _staff_member(
        "mt-staff-outsider@example.com", mt_tenant_b, "owner"
    )
    client = _connect_client()
    client_id = _staff_client()
    _verifier, challenge = _pkce()
    authorize = "/identity/o/authorize?" + _authorize_query(
        client_id, _resource(mt_tenant), challenge
    )
    _sign_in(client, outsider.email, authorize)

    response = client.get(authorize, secure=True)

    assert response.status_code == 403
    assert "form" not in (response.context or {})


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_an_unknown_store_is_refused_before_consent(
    mt_public_tenant, mt_tenant
):
    owner = _staff_member("mt-staff-unknown@example.com", mt_tenant, "owner")
    client = _connect_client()
    client_id = _staff_client()
    _verifier, challenge = _pkce()
    authorize = "/identity/o/authorize?" + _authorize_query(
        client_id, "https://not-a-store.test/mcp/admin", challenge
    )
    _sign_in(client, owner.email, authorize)

    response = client.get(authorize, secure=True)

    assert response.status_code == 400


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_store_that_turns_it_off_refuses_consent_and_connected_apps(
    mt_public_tenant, mt_tenant
):
    owner = _staff_member("mt-staff-switch@example.com", mt_tenant, "owner")
    token = _staff_token(owner, mt_tenant, ALL_READ)
    client = _connect_client()
    client_id = _staff_client()
    _verifier, challenge = _pkce()
    authorize = "/identity/o/authorize?" + _authorize_query(
        client_id, _resource(mt_tenant), challenge
    )
    _sign_in(client, owner.email, authorize)
    assert client.get(authorize, secure=True).status_code == 200

    _allow(mt_tenant, on=False)

    assert client.get(authorize, secure=True).status_code == 403
    for name in STAFF_ENDPOINTS:
        assert _get(mt_tenant, name, token).status_code == 404, name


# ── the token endpoint and its resource (RFC 8707) ──────────────────


def _authorization_code(
    mt_tenant, email: str
) -> tuple[HttpClient, str, str, str]:
    """Sign a staff member in, grant consent, and return the connect
    client, the client id, the code and the PKCE verifier."""
    staff = _staff_member(email, mt_tenant, "staff")
    client = _connect_client()
    client_id = _staff_client()
    verifier, challenge = _pkce()
    authorize = "/identity/o/authorize?" + _authorize_query(
        client_id, _resource(mt_tenant), challenge
    )
    _sign_in(client, staff.email, authorize)
    consent = client.get(authorize, secure=True)
    assert consent.status_code == 200
    granted = client.post(
        "/identity/o/authorize",
        {
            "request": consent.context["form"].initial["request"],
            "scopes": ["admin:orders:read"],
            "action": "grant",
        },
        secure=True,
    )
    assert granted.status_code == 302, granted.content
    code = parse_qs(urlsplit(granted["Location"]).query)["code"][0]
    return client, client_id, code, verifier


def _exchange(
    client: HttpClient, client_id: str, code: str, verifier: str, **extra
):
    return client.post(
        "/identity/o/api/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            **extra,
        },
        secure=True,
    )


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_code_exchange_without_a_resource_is_invalid_target_not_a_500(
    mt_public_tenant, mt_tenant
):
    """The staff issuer binds a token to one store, and MCP clients send
    the resource on the token request, so one without it is refused with
    the error RFC 8707 names for a missing resource."""
    client, client_id, code, verifier = _authorization_code(
        mt_tenant, "mt-staff-noresource@example.com"
    )

    token = _exchange(client, client_id, code, verifier)

    assert token.status_code == 400, token.content
    assert token.json()["error"] == "invalid_target"
    assert "required" in token.json()["error_description"]


def _connected(mt_tenant, email: str) -> tuple[HttpClient, str, dict[str, str]]:
    """A staff connection to *mt_tenant*: the connect client, the client
    id and the first token response."""
    client, client_id, code, verifier = _authorization_code(mt_tenant, email)
    first = _exchange(
        client, client_id, code, verifier, resource=_resource(mt_tenant)
    )
    assert first.status_code == 200, first.content
    return client, client_id, first.json()


def _refresh(client: HttpClient, client_id: str, refresh_token: str, **extra):
    return client.post(
        "/identity/o/api/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            **extra,
        },
        secure=True,
    )


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_refresh_without_a_resource_keeps_its_grants_store(
    mt_public_tenant, mt_tenant
):
    """RFC 8707 leaves the resource to the client on a refresh, and some
    MCP connectors omit it there: the token is issued for the store the
    grant was connected to, as is the refresh token that replaces it."""
    from allauth.idp.oidc.models import Token

    client, client_id, first = _connected(
        mt_tenant, "mt-staff-refresh-bare@example.com"
    )

    refreshed = _refresh(client, client_id, first["refresh_token"])

    assert refreshed.status_code == 200, refreshed.content
    access_token = refreshed.json()["access_token"]
    with schema_context(get_public_schema_name()):
        issued = Token.objects.lookup(Token.Type.ACCESS_TOKEN, access_token)
        assert issued is not None
        assert issued.get_resources() == [_resource(mt_tenant)]
    orders = _get(mt_tenant, "agent-staff-orders", access_token)
    assert orders.status_code == 200, orders.content

    again = _refresh(client, client_id, refreshed.json()["refresh_token"])
    assert again.status_code == 200, again.content
    orders = _get(mt_tenant, "agent-staff-orders", again.json()["access_token"])
    assert orders.status_code == 200, orders.content


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_refresh_naming_its_grants_store_is_issued(
    mt_public_tenant, mt_tenant
):
    client, client_id, first = _connected(
        mt_tenant, "mt-staff-refresh-same@example.com"
    )

    refreshed = _refresh(
        client, client_id, first["refresh_token"], resource=_resource(mt_tenant)
    )

    assert refreshed.status_code == 200, refreshed.content
    orders = _get(
        mt_tenant, "agent-staff-orders", refreshed.json()["access_token"]
    )
    assert orders.status_code == 200, orders.content


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_refresh_naming_another_store_is_invalid_target(
    mt_public_tenant, mt_tenant, mt_tenant_b
):
    """A refresh never widens its grant (RFC 8707 section 2.2), even to a
    store the person is on the team of."""
    email = "mt-staff-refresh-other@example.com"
    client, client_id, first = _connected(mt_tenant, email)
    _join_team(email, mt_tenant_b)

    refreshed = _refresh(
        client,
        client_id,
        first["refresh_token"],
        resource=_resource(mt_tenant_b),
    )

    assert refreshed.status_code == 400, refreshed.content
    assert refreshed.json()["error"] == "invalid_target"


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
@pytest.mark.parametrize(
    "resource",
    [
        "https://not-a-store.test/mcp/admin",
        "not-an-absolute-uri",
        "https://shop.test/mcp/admin#fragment",
    ],
)
def test_a_resource_the_issuer_refuses_is_invalid_target_not_a_500(
    mt_public_tenant, mt_tenant, resource
):
    client, client_id, code, verifier = _authorization_code(
        mt_tenant, f"mt-staff-bad-{abs(hash(resource))}@example.com"
    )

    token = _exchange(client, client_id, code, verifier, resource=resource)

    assert token.status_code == 400, token.content
    assert token.json()["error"] == "invalid_target"
    assert token.json()["error_description"]


@pytest.mark.parametrize(
    "path", ["/identity/o/api/token", "/identity/o/api/device/code"]
)
def test_a_store_issuer_answers_a_malformed_resource_with_invalid_target(
    mt_tenant, path
):
    response = TenantClient(mt_tenant).post(
        path,
        {
            "grant_type": "authorization_code",
            "client_id": "unknown",
            "resource": "not-an-absolute-uri",
        },
        secure=True,
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "invalid_target"


@override_settings(STAFF_CONNECT_HOST=CONNECT_HOST)
def test_a_code_stays_redeemable_after_a_refused_resource(
    mt_public_tenant, mt_tenant
):
    """The refusal comes before oauthlib looks at the code, so a client
    that sent a bad or missing resource can retry with the right one."""
    client, client_id, code, verifier = _authorization_code(
        mt_tenant, "mt-staff-retry@example.com"
    )

    missing = _exchange(client, client_id, code, verifier)
    bad = _exchange(
        client, client_id, code, verifier, resource="not-an-absolute-uri"
    )
    assert missing.status_code == bad.status_code == 400
    assert missing.json()["error"] == bad.json()["error"] == "invalid_target"

    redeemed = _exchange(
        client, client_id, code, verifier, resource=_resource(mt_tenant)
    )

    assert redeemed.status_code == 200, redeemed.content


def test_a_store_issuer_refuses_another_stores_resource(mt_tenant, mt_tenant_b):
    other = f"https://{mt_tenant_b.get_primary_domain().domain}/mcp"

    response = TenantClient(mt_tenant).post(
        "/identity/o/api/token",
        {
            "grant_type": "authorization_code",
            "client_id": "x",
            "resource": other,
        },
        secure=True,
    )

    assert response.status_code == 400, response.content
    assert response.json()["error"] == "invalid_target"
    assert "this store" in response.json()["error_description"]
