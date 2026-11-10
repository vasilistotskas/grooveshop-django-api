"""The store-admin agent surface (``/api/v1/agent/staff/*``).

Every refusal path, on every endpoint, and what each endpoint reads. The
schema-level claims (a customer token is never found where staff tokens
are looked up; the connect host serves only the staff issuer) need the
real schemas and live in ``tests_mt/test_staff_agent_schema_binding.py``.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

from allauth.idp.oidc.models import Client, Token
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from agent.oidc import AgentOIDCAdapter
from agent.staff import STAFF_CLIENT_SCOPES, STAFF_SCOPE_PERMISSIONS
from contact.models import Contact
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from tenant.models import (
    Tenant,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

ALL_READ = list(STAFF_SCOPE_PERMISSIONS)
DOMAIN = "staff-agent.example"
RESOURCE = f"https://{DOMAIN}/mcp/admin"

ENDPOINTS = {
    "agent-staff-orders": "admin:orders:read",
    "agent-staff-sales": "admin:sales:read",
    "agent-staff-profit": "admin:profit:read",
    "agent-staff-products": "admin:catalog:read",
    "agent-staff-customers": "admin:customers:read",
    "agent-staff-reviews": "admin:reviews:read",
    "agent-staff-messages": "admin:messages:read",
}


class StaffAgentAPITestCase(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.tenant = Tenant(
            schema_name="staff_agent_tenant",
            name="Staff Agent Tenant",
            slug="staff-agent-tenant",
            owner_email="owner-staff-agent@example.com",
        )
        cls.tenant.auto_create_schema = False
        cls.tenant.save()
        TenantDomain.objects.create(
            tenant=cls.tenant, domain=DOMAIN, is_primary=True
        )
        cls.other_tenant = Tenant(
            schema_name="staff_agent_other",
            name="Other Store",
            slug="staff-agent-other",
            owner_email="owner-staff-other@example.com",
        )
        cls.other_tenant.auto_create_schema = False
        cls.other_tenant.save()
        TenantDomain.objects.create(
            tenant=cls.other_tenant,
            domain="other-staff.example",
            is_primary=True,
        )
        cls.oidc_client = Client.objects.create(name="Staff agent")
        cls.oidc_client.set_scopes(list(STAFF_CLIENT_SCOPES))
        cls.oidc_client.save()
        # The store has let its team connect an AI app.
        switch_store_settings(STAFF_AI_ACCESS_ENABLED=True)

    def setUp(self) -> None:
        # set_tenant() keeps connection.tenant and schema_name in step;
        # see tests/unit/agent/test_agent_api.py.
        self._prev_tenant = getattr(connection, "tenant", None)
        connection.set_tenant(self.tenant)
        self.addCleanup(self._restore_connection_tenant)

    def _restore_connection_tenant(self) -> None:
        if self._prev_tenant is not None:
            connection.set_tenant(self._prev_tenant)
        else:
            connection.set_schema_to_public()

    def _member(self, role: str, *, tenant=None, **user_fields):
        user = UserAccountFactory(is_staff=True, **user_fields)
        UserTenantMembership.objects.create(
            user=user, tenant=tenant or self.tenant, role=role, is_active=True
        )
        return user

    def _bearer(
        self,
        user,
        scopes=None,
        *,
        resources=(RESOURCE,),
        token_type=Token.Type.ACCESS_TOKEN,
        expires_in: int = 3600,
    ) -> Token:
        raw = secrets.token_urlsafe(32)
        token = Token(
            type=token_type,
            client=self.oidc_client,
            user=user,
            expires_at=timezone.now() + timedelta(seconds=expires_in),
        )
        token.set_value(raw)
        token.set_scopes(ALL_READ if scopes is None else scopes)
        if resources:
            token.set_resources(list(resources))
        token.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return token

    def _assert_challenged(self, name: str) -> None:
        response = self.client.get(reverse(name))
        self.assertEqual(
            response.status_code, status.HTTP_401_UNAUTHORIZED, name
        )
        self.assertEqual(
            response["WWW-Authenticate"], 'Bearer realm="agent-staff"'
        )

    # ── refusals ───────────────────────────────────────────────────

    def test_a_store_that_has_not_turned_it_on_serves_nothing(self) -> None:
        """Off is a bare 404 on the whole surface, a connected app's valid
        token included, and ``me``, which the gateway verifies against."""
        switch_store_settings(STAFF_AI_ACCESS_ENABLED=False)
        owner = self._member(TenantMembershipRole.OWNER)
        self._bearer(owner)
        for name in ["agent-staff-me", *ENDPOINTS]:
            response = self.client.get(reverse(name))
            self.assertEqual(
                response.status_code, status.HTTP_404_NOT_FOUND, name
            )

    def test_anonymous_is_challenged_everywhere(self) -> None:
        for name in ["agent-staff-me", *ENDPOINTS]:
            self._assert_challenged(name)

    def test_a_token_for_another_stores_tools_is_refused(self) -> None:
        owner = self._member(TenantMembershipRole.OWNER)
        self._bearer(
            owner, resources=("https://other-staff.example/mcp/admin",)
        )
        for name in ["agent-staff-me", *ENDPOINTS]:
            self._assert_challenged(name)

    def test_a_token_bound_to_the_shopper_server_is_refused(self) -> None:
        owner = self._member(TenantMembershipRole.OWNER)
        self._bearer(owner, resources=(f"https://{DOMAIN}/mcp",))
        self._assert_challenged("agent-staff-orders")

    def test_a_token_bound_to_nothing_is_refused(self) -> None:
        owner = self._member(TenantMembershipRole.OWNER)
        self._bearer(owner, resources=())
        self._assert_challenged("agent-staff-orders")

    def test_staff_of_another_store_is_refused(self) -> None:
        outsider = self._member(
            TenantMembershipRole.OWNER, tenant=self.other_tenant
        )
        self._bearer(outsider)
        for name in ["agent-staff-me", *ENDPOINTS]:
            self._assert_challenged(name)

    def test_an_inactive_membership_is_refused(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        UserTenantMembership.objects.filter(user=staff).update(is_active=False)
        self._bearer(staff)
        self._assert_challenged("agent-staff-orders")

    def test_revoked_staff_status_is_refused(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        staff.is_staff = False
        staff.save(update_fields=["is_staff"])
        self._bearer(staff)
        self._assert_challenged("agent-staff-orders")

    def test_an_inactive_account_is_refused(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        staff.is_active = False
        staff.save(update_fields=["is_active"])
        self._bearer(staff)
        self._assert_challenged("agent-staff-orders")

    def test_an_expired_token_is_refused(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, expires_in=-60)
        self._assert_challenged("agent-staff-orders")

    def test_a_revoked_token_is_refused(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff).delete()
        self._assert_challenged("agent-staff-orders")

    def test_a_refresh_token_is_not_an_access_token(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, token_type=Token.Type.REFRESH_TOKEN)
        self._assert_challenged("agent-staff-orders")

    def test_a_scope_not_granted_is_forbidden(self) -> None:
        owner = self._member(TenantMembershipRole.OWNER)
        for name, scope in ENDPOINTS.items():
            self._bearer(owner, [s for s in ALL_READ if s != scope])
            response = self.client.get(reverse(name))
            self.assertEqual(
                response.status_code, status.HTTP_403_FORBIDDEN, name
            )

    def test_the_role_decides_what_opens(self) -> None:
        """Each role against each endpoint, with every scope granted."""
        closed = {
            TenantMembershipRole.STAFF: {
                "agent-staff-sales",
                "agent-staff-profit",
            },
            TenantMembershipRole.ADMIN: {"agent-staff-profit"},
            TenantMembershipRole.OWNER: set(),
        }
        for role, denied in closed.items():
            user = self._member(role)
            self._bearer(user)
            for name in ENDPOINTS:
                response = self.client.get(reverse(name))
                expected = (
                    status.HTTP_403_FORBIDDEN
                    if name in denied
                    else status.HTTP_200_OK
                )
                self.assertEqual(response.status_code, expected, (role, name))

    def test_a_superuser_gets_their_store_role_only(self) -> None:
        """No superuser short-circuit through an AI app's token."""
        superuser = self._member(TenantMembershipRole.STAFF, is_superuser=True)
        self._bearer(superuser)
        response = self.client.get(reverse("agent-staff-profit"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── what the tools read ────────────────────────────────────────

    def test_me_names_the_store_role_and_scopes(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:orders:read", "admin:sales:read"])

        response = self.client.get(reverse("agent-staff-me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], "staff")
        self.assertEqual(response.data["store_domain"], DOMAIN)
        scopes = {row["scope"]: row for row in response.data["scopes"]}
        self.assertTrue(scopes["admin:orders:read"]["granted"])
        self.assertTrue(scopes["admin:orders:read"]["allowed"])
        self.assertTrue(scopes["admin:sales:read"]["granted"])
        self.assertFalse(scopes["admin:sales:read"]["allowed"])
        self.assertFalse(scopes["admin:profit:read"]["granted"])

    def test_orders_filter_and_never_carry_the_street_or_phone(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:orders:read"])
        order = OrderFactory(status="PROCESSING", num_order_items=1)
        OrderFactory(status="CANCELED", num_order_items=0)

        listed = self.client.get(
            reverse("agent-staff-orders"), {"status": "PROCESSING"}
        )
        detail = self.client.get(reverse("agent-staff-order", args=[order.id]))

        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in listed.data], [order.id])
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["customer_email"], order.email)
        for leaked in ("street", "street_number", "phone", "zipcode"):
            self.assertNotIn(leaked, detail.data)
        body = detail.content.decode()
        self.assertNotIn(order.street, body)

    def test_a_non_ascii_digit_search_is_a_text_search(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:orders:read", "admin:catalog:read"])
        for name in ("agent-staff-orders", "agent-staff-products"):
            response = self.client.get(reverse(name), {"search": "²"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, name)

    def test_listing_orders_costs_the_same_for_one_or_many(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:orders:read"])
        url = reverse("agent-staff-orders")
        OrderFactory(num_order_items=2)
        with CaptureQueriesContext(connection) as one:
            self.client.get(url)
        OrderFactory.create_batch(4, num_order_items=2)
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(url)

        self.assertEqual(len(response.data), 5)
        self.assertEqual(len(many), len(one))

    def test_orders_refuse_an_unknown_status_and_a_bad_range(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:orders:read"])
        url = reverse("agent-staff-orders")

        self.assertEqual(
            self.client.get(url, {"status": "LOST"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.get(
                url, {"placedFrom": "2026-02-01", "placedTo": "2026-01-01"}
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.get(url, {"limit": "0"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_the_sales_report_is_the_admins_turnover(self) -> None:
        from order import turnover

        admin = self._member(TenantMembershipRole.ADMIN)
        self._bearer(admin, ["admin:sales:read"])

        response = self.client.get(
            reverse("agent-staff-sales"),
            {"start": "2026-01-01", "end": "2026-03-31", "bucket": "month"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = turnover.figures(
            date(2026, 1, 1), date(2026, 3, 31), turnover.Bucket.MONTH
        )
        self.assertEqual(len(response.data["rows"]), len(expected))
        self.assertEqual(response.data["bucket"], "month")

    def test_low_stock_lists_only_products_at_their_threshold(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:catalog:read"])
        low = ProductFactory(stock=1, low_stock_threshold=5)
        ProductFactory(stock=50, low_stock_threshold=5)

        response = self.client.get(
            reverse("agent-staff-products"), {"lowStock": "true"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [low.id])
        self.assertTrue(response.data[0]["low_stock"])

    def test_customers_carry_no_address_or_phone(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:customers:read"])
        customer = UserAccountFactory(
            email="findme-staff-agent@example.com", address="Odos 1"
        )

        response = self.client.get(
            reverse("agent-staff-customers"), {"search": "findme-staff"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [customer.id])
        self.assertNotIn("Odos 1", response.content.decode())

    def test_messages_list_newest_first(self) -> None:
        staff = self._member(TenantMembershipRole.STAFF)
        self._bearer(staff, ["admin:messages:read"])
        Contact.objects.create(name="A", email="a@example.com", message="one")
        newer = Contact.objects.create(
            name="B", email="b@example.com", message="two"
        )

        response = self.client.get(
            reverse("agent-staff-messages"), {"limit": 1}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [newer.id])


class StaffIssuerAdapterTestCase(APITestCase):
    """The adapter's platform-issuer branch (the connection on public)."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.tenant = Tenant(
            schema_name="staff_issuer_tenant",
            name="Staff Issuer Tenant",
            slug="staff-issuer-tenant",
            owner_email="owner-staff-issuer@example.com",
        )
        cls.tenant.auto_create_schema = False
        cls.tenant.save()
        TenantDomain.objects.create(
            tenant=cls.tenant, domain="issuer-store.example", is_primary=True
        )
        TenantDomain.objects.create(
            tenant=cls.tenant, domain="alias-store.example", is_primary=False
        )

    def setUp(self) -> None:
        connection.set_schema_to_public()

    def test_only_a_stores_primary_staff_server_is_a_resource(self) -> None:
        adapter = AgentOIDCAdapter()
        adapter.validate_resource_uris(
            uris=["https://issuer-store.example/mcp/admin"]
        )
        for uris in (
            [],
            ["https://issuer-store.example/mcp"],
            ["https://alias-store.example/mcp/admin"],
            ["http://issuer-store.example/mcp/admin"],
            ["https://issuer-store.example:8443/mcp/admin"],
            # Not the canonical form a call is compared with.
            ["https://Issuer-Store.example/mcp/admin"],
            ["https://unknown.example/mcp/admin"],
            [
                "https://issuer-store.example/mcp/admin",
                "https://issuer-store.example/mcp/admin/",
            ],
        ):
            with self.assertRaises(ValidationError, msg=uris):
                adapter.validate_resource_uris(uris=uris)

    def test_a_suspended_store_is_not_a_resource(self) -> None:
        Tenant.objects.filter(pk=self.tenant.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            AgentOIDCAdapter().validate_resource_uris(
                uris=["https://issuer-store.example/mcp/admin"]
            )

    def test_a_registration_gets_the_staff_scopes_and_the_code_flow(
        self,
    ) -> None:
        client = Client(name="Registering agent")
        client.set_scopes(["openid", "orders:read"])
        AgentOIDCAdapter().validate_client_registration(
            client=client, client_metadata={}, token=None, bearer_token=None
        )
        self.assertEqual(client.get_scopes(), list(STAFF_CLIENT_SCOPES))

        device = Client(name="Device agent")
        device.set_grant_types(["urn:ietf:params:oauth:grant-type:device_code"])
        with self.assertRaises(ValidationError):
            AgentOIDCAdapter().validate_client_registration(
                client=device,
                client_metadata={},
                token=None,
                bearer_token=None,
            )


def test_a_margin_of_any_size_serializes():
    """Cents of revenue against euros of cost: no 500 on the report."""
    from decimal import Decimal

    from agent.staff_serializers import StaffProfitRowSerializer

    row = dict.fromkeys(
        (
            "revenue",
            "cogs",
            "gross_profit",
            "payment_fees",
            "delivery_costs",
            "other_expenses",
            "operating_profit",
        ),
        Decimal("0.05"),
    )
    row.update(
        period=None,
        gross_margin=Decimal("-119900.0"),
        cost_coverage=None,
        fee_coverage=None,
    )
    data = StaffProfitRowSerializer(row).data
    assert data["gross_margin"] == Decimal("-119900.00")
