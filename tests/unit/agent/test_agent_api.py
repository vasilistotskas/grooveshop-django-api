"""Tests for the agent-facing scoped API (``/api/v1/agent/*``).

The agent surface accepts ONLY ``allauth.idp`` OIDC access tokens (the
tokens AI agents obtain by linking a shopper's account through the
authorization-code + PKCE flow). Knox / session auth must NOT work here
— the isolation is what keeps agent scopes least-privilege.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from allauth.idp.oidc.models import Client, Token

from order.factories.order import OrderFactory
from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from tenant.models import Tenant, TenantMembershipRole, UserTenantMembership
from user.factories.account import UserAccountFactory


def _mint_token(
    user, client: Client, scopes: list[str], expires_in: int = 3600
) -> str:
    raw = secrets.token_urlsafe(32)
    token = Token(
        type=Token.Type.ACCESS_TOKEN,
        client=client,
        user=user,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
    )
    token.set_value(raw)
    token.set_scopes(scopes)
    token.save()
    return raw


class AgentAPITestCase(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = UserAccountFactory()
        cls.oidc_client = Client.objects.create(name="Test agent")
        cls.oidc_client.set_scopes(
            ["profile", "orders:read", "loyalty:read", "favourites:read"]
        )
        cls.oidc_client.save()
        # The agent surface pairs every scope permission with
        # HasTenantAccess — build a tenant + active membership and bind
        # the tenant to the connection (unit tests strip the
        # TenantMainMiddleware that normally does this).
        cls.tenant = Tenant(
            schema_name="agent_test_tenant",
            name="Agent Test Tenant",
            slug="agent-test-tenant",
            owner_email="owner-agent-test@example.com",
        )
        cls.tenant.auto_create_schema = False
        cls.tenant.save()
        UserTenantMembership.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=True,
        )

    def setUp(self) -> None:
        from django.db import connection

        self._had_tenant = hasattr(connection, "tenant")
        self._prev_tenant = getattr(connection, "tenant", None)
        connection.tenant = self.tenant
        self.addCleanup(self._restore_connection_tenant)

    def _restore_connection_tenant(self) -> None:
        from django.db import connection

        if self._had_tenant:
            connection.tenant = self._prev_tenant
        elif hasattr(connection, "tenant"):
            del connection.tenant

    def _bearer(self, scopes: list[str], **kwargs) -> None:
        raw = _mint_token(self.user, self.oidc_client, scopes, **kwargs)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    def test_anonymous_is_rejected_with_401_challenge(self) -> None:
        # Exactly 401 with a Bearer challenge — the agent gateway relies
        # on the 401 to trigger the RFC 9728 re-auth flow; a 403 would
        # make a bad token look like a merely under-scoped one.
        for name in (
            "agent-me",
            "agent-orders",
            "agent-loyalty",
            "agent-favourites",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(
                response.status_code, status.HTTP_401_UNAUTHORIZED, name
            )
            self.assertIn("Bearer", response["WWW-Authenticate"])

    def test_invalid_token_is_401(self) -> None:
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        response = self.client.get(reverse("agent-me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_scope_is_rejected(self) -> None:
        self._bearer(["profile"])
        response = self.client.get(reverse("agent-orders"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_expired_token_is_401(self) -> None:
        self._bearer(["profile"], expires_in=-60)
        response = self.client.get(reverse("agent-me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_session_auth_is_not_accepted(self) -> None:
        # The agent surface must be OIDC-token-only: a logged-in browser
        # session (or any non-OIDC credential) does not authenticate.
        self.client.force_login(self.user)
        response = self.client.get(reverse("agent-me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_linked_profile(self) -> None:
        self._bearer(["profile"])
        response = self.client.get(reverse("agent-me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.user.id)
        self.assertEqual(response.data["email"], self.user.email)

    def test_orders_returns_only_own_orders(self) -> None:
        own = OrderFactory(user=self.user)
        OrderFactory(user=UserAccountFactory())  # someone else's
        self._bearer(["orders:read"])
        response = self.client.get(reverse("agent-orders"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row["id"] for row in response.data]
        self.assertIn(own.id, ids)
        self.assertEqual(len(ids), 1)

    def test_favourites_lists_active_products_only(self) -> None:
        active = ProductFactory(active=True, stock=5)
        inactive = ProductFactory(active=False)
        ProductFavouriteFactory(user=self.user, product=active)
        ProductFavouriteFactory(user=self.user, product=inactive)
        ProductFavouriteFactory(product=active)  # someone else's

        self._bearer(["favourites:read"])
        response = self.client.get(reverse("agent-favourites"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["product_id"], active.id)
        self.assertTrue(row["in_stock"])
        for key in ("name", "final_price", "currency", "added_at"):
            self.assertIn(key, row)

    def test_favourites_requires_scope(self) -> None:
        self._bearer(["profile"])
        response = self.client.get(reverse("agent-favourites"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_loyalty_returns_summary_shape(self) -> None:
        self._bearer(["loyalty:read"])
        response = self.client.get(reverse("agent-loyalty"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in (
            "points_balance",
            "total_xp",
            "level",
            "tier",
            "points_to_next_tier",
        ):
            self.assertIn(key, response.data)

    def test_valid_token_needs_no_membership(self) -> None:
        # Membership is a STAFF grant held by platform-public
        # identities; shoppers hold none and cannot (the table is in the
        # public schema with an FK to the public user table, while
        # shoppers live in the tenant schema). Requiring one here denied
        # every legitimate customer.
        #
        # Isolation is structural instead: allauth.idp.oidc is in
        # TENANT_APPS, so a token issued by one store does not exist in
        # another's tables and cannot authenticate there at all.
        UserTenantMembership.objects.filter(
            user=self.user, tenant=self.tenant
        ).update(is_active=False)
        self._bearer(["profile", "orders:read"])
        for name in ("agent-me", "agent-orders"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, status.HTTP_200_OK, name)


class AuthorizeLoginRedirectTestCase(APITestCase):
    def test_anonymous_authorize_redirects_to_shopper_login(self) -> None:
        """The OIDC authorize view must send anonymous users to allauth's
        shopper login — the admin login (the old global LOGIN_URL) rejects
        non-staff, which would dead-end every account-linking flow."""
        response = self.client.get("/identity/o/authorize", {"client_id": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith("/accounts/login/"),
            response["Location"],
        )
