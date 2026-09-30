"""The agent surface accepts OIDC access tokens and nothing else.

Covers ``agent/views.py`` (``AgentAPIView`` / ``AgentTokenAuthentication``):
a valid Knox token, an OIDC refresh token, a query-string access token and
an inactive user's token must all be refused with the 401 challenge the
agent gateway relies on.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from allauth.idp.oidc.models import Client, Token
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.models import AuthToken
from rest_framework import status
from rest_framework.test import APITestCase

from tenant.models import Tenant
from user.factories.account import UserAccountFactory


def _mint(user, client: Client, token_type: str, scopes: list[str]) -> str:
    raw = secrets.token_urlsafe(32)
    token = Token(
        type=token_type,
        client=client,
        user=user,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    token.set_value(raw)
    token.set_scopes(scopes)
    token.save()
    return raw


class AgentAuthIsolationTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = UserAccountFactory()
        cls.oidc_client = Client.objects.create(name="Isolation agent")
        cls.oidc_client.set_scopes(["profile", "orders:read"])
        cls.oidc_client.save()
        cls.tenant = Tenant(
            schema_name="agent_isolation_tenant",
            name="Agent Isolation Tenant",
            slug="agent-isolation-tenant",
            owner_email="owner-agent-isolation@example.com",
        )
        cls.tenant.auto_create_schema = False
        cls.tenant.save()

    def setUp(self) -> None:
        # set_tenant() keeps connection.tenant and schema_name in step;
        # see tests/unit/agent/test_agent_api.py for why that matters.
        self._prev_tenant = getattr(connection, "tenant", None)
        connection.set_tenant(self.tenant)
        self.addCleanup(self._restore_connection_tenant)

    def _restore_connection_tenant(self) -> None:
        if self._prev_tenant is not None:
            connection.set_tenant(self._prev_tenant)
        else:
            connection.set_schema_to_public()

    def _assert_challenged(self, response) -> None:
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="agent"')

    def test_valid_oidc_access_token_is_accepted(self) -> None:
        raw = _mint(
            self.user, self.oidc_client, Token.Type.ACCESS_TOKEN, ["profile"]
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        response = self.client.get(reverse("agent-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.user.id)

    def test_valid_knox_token_is_rejected(self) -> None:
        _instance, knox_token = AuthToken.objects.create(self.user)
        # The token is genuinely valid for the storefront auth stack...
        knox_user, _ = KnoxTokenAuthentication().authenticate_credentials(
            knox_token.encode()
        )
        self.assertEqual(knox_user, self.user)

        # ...and still does not open the agent surface.
        for name in ("agent-me", "agent-orders"):
            for scheme in ("Bearer", "Token"):
                self.client.credentials(
                    HTTP_AUTHORIZATION=f"{scheme} {knox_token}"
                )
                self._assert_challenged(self.client.get(reverse(name)))

    def test_refresh_token_cannot_be_used_as_access_token(self) -> None:
        raw = _mint(
            self.user,
            self.oidc_client,
            Token.Type.REFRESH_TOKEN,
            ["profile", "orders:read"],
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        self._assert_challenged(self.client.get(reverse("agent-me")))

    def test_access_token_in_query_string_is_rejected(self) -> None:
        raw = _mint(
            self.user, self.oidc_client, Token.Type.ACCESS_TOKEN, ["profile"]
        )

        response = self.client.get(reverse("agent-me"), {"access_token": raw})

        self._assert_challenged(response)

    def test_token_of_a_deactivated_user_is_rejected(self) -> None:
        raw = _mint(
            self.user, self.oidc_client, Token.Type.ACCESS_TOKEN, ["profile"]
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        self._assert_challenged(self.client.get(reverse("agent-me")))
