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
