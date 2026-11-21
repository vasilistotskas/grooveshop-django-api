"""The store issuer registers only the grants identity linking uses, and
the agent API refuses a token that names no account."""

from __future__ import annotations

import secrets
from datetime import timedelta
from unittest.mock import patch

from allauth.idp.oidc.models import Client, Token
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from agent.checks import CIMD_ENABLED, client_metadata_documents_stay_off
from agent.oidc import CLIENT_GRANT_TYPES, AgentOIDCAdapter
from agent.staff import STAFF_CLIENT_SCOPES
from tests.unit.agent.test_agent_api import AgentAPITestCase


class StoreIssuerGrantsTestCase(AgentAPITestCase):
    def _register(self, grant_types: list[str]) -> Client:
        client = Client(name="Registering agent")
        client.set_grant_types(grant_types)
        AgentOIDCAdapter().validate_client_registration(
            client=client, client_metadata={}, token=None, bearer_token=None
        )
        return client

    def test_the_code_flow_and_its_refresh_register(self) -> None:
        client = self._register(["authorization_code", "refresh_token"])
        self.assertEqual(
            set(client.get_grant_types()),
            {"authorization_code", "refresh_token"},
        )

    def test_any_other_grant_is_refused(self) -> None:
        for grants in (
            ["client_credentials"],
            ["authorization_code", "client_credentials"],
            ["urn:ietf:params:oauth:grant-type:device_code"],
        ):
            with self.subTest(grants), self.assertRaises(ValidationError):
                self._register(grants)

    def test_registration_over_http_refuses_client_credentials(self) -> None:
        response = self.client.post(
            "/identity/o/api/clients",
            {
                "redirect_uris": ["https://agent.example/callback"],
                "grant_types": ["client_credentials"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"], "invalid_client_metadata")

    def test_the_metadata_names_the_grants(self) -> None:
        data: dict = {}
        AgentOIDCAdapter().populate_server_metadata(data)
        self.assertEqual(
            data["grant_types_supported"], sorted(CLIENT_GRANT_TYPES)
        )


class PlatformIssuerGrantsTestCase(AgentAPITestCase):
    def test_the_platform_issuer_holds_the_same_grants_and_the_staff_scopes(
        self,
    ) -> None:
        with patch("agent.oidc.on_platform_issuer", return_value=True):
            client = Client(name="Registering agent")
            client.set_grant_types(["refresh_token"])
            AgentOIDCAdapter().validate_client_registration(
                client=client,
                client_metadata={},
                token=None,
                bearer_token=None,
            )
            self.assertEqual(client.get_scopes(), list(STAFF_CLIENT_SCOPES))

            device = Client(name="Device agent")
            device.set_grant_types(
                ["urn:ietf:params:oauth:grant-type:device_code"]
            )
            with self.assertRaises(ValidationError):
                AgentOIDCAdapter().validate_client_registration(
                    client=device,
                    client_metadata={},
                    token=None,
                    bearer_token=None,
                )

            data: dict = {"device_authorization_endpoint": "x"}
            AgentOIDCAdapter().populate_server_metadata(data)
            self.assertEqual(
                data["scopes_supported"], list(STAFF_CLIENT_SCOPES)
            )
            self.assertEqual(
                data["grant_types_supported"], sorted(CLIENT_GRANT_TYPES)
            )
            self.assertNotIn("device_authorization_endpoint", data)


class TokenWithoutAUserTestCase(AgentAPITestCase):
    def test_a_token_that_names_no_account_is_a_401(self) -> None:
        raw = secrets.token_urlsafe(32)
        token = Token(
            type=Token.Type.ACCESS_TOKEN,
            client=self.oidc_client,
            user=None,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        token.set_value(raw)
        token.set_scopes(["profile", "orders:read"])
        token.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

        for name in ("agent-me", "agent-orders"):
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertEqual(
                    response.status_code, status.HTTP_401_UNAUTHORIZED
                )
                self.assertEqual(
                    response["WWW-Authenticate"], 'Bearer realm="agent"'
                )


class ClientMetadataDocumentsTestCase(SimpleTestCase):
    def test_they_are_off_by_default(self) -> None:
        self.assertEqual(client_metadata_documents_stay_off(None), [])

    @override_settings(IDP_OIDC_CIMD_ENABLED=True)
    def test_enabling_them_fails_the_check(self) -> None:
        errors = client_metadata_documents_stay_off(None)

        self.assertEqual([error.id for error in errors], [CIMD_ENABLED])
