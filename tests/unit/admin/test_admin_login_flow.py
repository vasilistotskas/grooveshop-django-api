"""E2E-style tests for the platform-staff-only admin login flow.

Exercises the real HTTP login view (``django.test.Client``) end to
end — POST credentials, follow the resulting session, then GET
``/admin/``. See ``tests/unit/admin/test_admin_site_permissions.py``
for narrower ``has_permission()``-level unit tests.

NOTE on "host" semantics: ``tests/conftest.py`` strips
``django_tenants.middleware.main.TenantMainMiddleware`` and sets
``DATABASE_ROUTERS = []`` for the WHOLE test session — no test in this
suite gets real hostname-based schema routing (see the ``bind_tenant``
fixture docstring in ``tests/conftest.py``). These tests simulate
"being on tenant X's host" the same way the rest of the suite does:
binding ``connection.tenant`` directly. ``SERVER_NAME`` is set on each
request for readability/documentation only — it has no bearing on
tenant resolution under this harness.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.urls import reverse
from django_tenants.utils import get_public_schema_name

from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

LOGIN_URL = "/admin/login/"
INDEX_URL = "/admin/"


@pytest.fixture
def tenant_a(db):
    t = Tenant(
        schema_name="loginflow_tenant_a",
        name="Login Flow Tenant A",
        slug="loginflow-tenant-a",
        owner_email="owner-a@example.com",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def tenant_b(db):
    t = Tenant(
        schema_name="loginflow_tenant_b",
        name="Login Flow Tenant B",
        slug="loginflow-tenant-b",
        owner_email="owner-b@example.com",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def bind_tenant(monkeypatch):
    """Bind ``connection.tenant`` (and ``.schema_name``) for one test.

    The views exercised here (``PlatformStaffBackend``, the admin
    login form, ``password_change``) call REAL
    ``django_tenants.utils.schema_context()`` internally, whose
    ``__exit__`` restores the PREVIOUS ``connection.tenant``/
    ``schema_name`` via a real ``connection.set_tenant()`` call — a
    genuine, persistent mutation of the shared connection object, not
    something ``monkeypatch`` tracks on its own. Patching
    ``schema_name`` too (alongside ``tenant``) makes sure monkeypatch's
    teardown restores BOTH attributes, so this doesn't leak
    ``connection.schema_name`` into whichever test runs next in the
    same xdist worker.
    """

    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)
        monkeypatch.setattr(
            connection,
            "schema_name",
            getattr(t, "schema_name", None) or get_public_schema_name(),
            raising=False,
        )

    return _bind


def _login(client, *, username, password, server_name):
    # AdminSite.login() only pre-fills the "next" hidden field for GET
    # rendering; LOGIN_REDIRECT_URL points at the Nuxt storefront, so
    # the POST must carry "next" explicitly to land back on /admin/.
    return client.post(
        LOGIN_URL,
        {"username": username, "password": password, "next": INDEX_URL},
        SERVER_NAME=server_name,
    )


class TestAdminLoginFlow:
    def test_staff_with_membership_reaches_admin_index(
        self, tenant_a, bind_tenant
    ):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant_a,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )
        bind_tenant(tenant_a)

        client = Client()
        response = _login(
            client,
            username=staff.email,
            password="pw12345",
            server_name="tenant-a.example.com",
        )
        assert response.status_code == 302
        assert response.url == INDEX_URL

        index_response = client.get(
            INDEX_URL, SERVER_NAME="tenant-a.example.com"
        )
        assert index_response.status_code == 200

    def test_staff_without_membership_login_ok_but_admin_denied(
        self, tenant_a, tenant_b, bind_tenant
    ):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant_a,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )

        client = Client()
        # PlatformStaffBackend only checks is_active/is_staff on the
        # PUBLIC row — not tenant membership — so authentication
        # succeeds regardless of which tenant is bound.
        bind_tenant(tenant_b)
        response = _login(
            client,
            username=staff.email,
            password="pw12345",
            server_name="tenant-b.example.com",
        )
        assert response.status_code == 302
        assert response.url == INDEX_URL

        index_response = client.get(
            INDEX_URL, SERVER_NAME="tenant-b.example.com"
        )
        # has_permission() denies (no membership on tenant B) —
        # redirected away from the index instead of rendering it.
        assert index_response.status_code == 302
        assert "login" in index_response.url

    def test_tenant_schema_customer_credentials_rejected(
        self, tenant_a, bind_tenant
    ):
        # Stands in for a tenant-schema customer: an ordinary,
        # non-staff UserAccount. PlatformStaffBackend only matches
        # PUBLIC-schema is_staff users, so this never authenticates.
        customer = UserAccountFactory(is_staff=False, plain_password="pw12345")
        bind_tenant(tenant_a)

        client = Client()
        response = _login(
            client,
            username=customer.email,
            password="pw12345",
            server_name="tenant-a.example.com",
        )
        assert response.status_code == 200  # re-renders the login form
        assert response.context["form"].errors

    def test_superuser_passes_on_any_tenant_host(self, tenant_a, bind_tenant):
        superuser = UserAccountFactory(
            is_staff=True, is_superuser=True, plain_password="pw12345"
        )
        bind_tenant(tenant_a)

        client = Client()
        _login(
            client,
            username=superuser.email,
            password="pw12345",
            server_name="tenant-a.example.com",
        )
        index_response = client.get(
            INDEX_URL, SERVER_NAME="tenant-a.example.com"
        )
        assert index_response.status_code == 200

    def test_force_login_non_platform_backend_denied_even_for_staff_superuser(
        self, tenant_a, bind_tenant
    ):
        """A session NOT authenticated via PlatformStaffBackend is
        denied regardless of is_staff/is_superuser — the backend-
        session guard is authoritative (closes the pk-collision
        ambiguity a plain role/flag check can't)."""
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        bind_tenant(tenant_a)

        client = Client()
        client.force_login(
            staff, backend="django.contrib.auth.backends.ModelBackend"
        )
        index_response = client.get(
            INDEX_URL, SERVER_NAME="tenant-a.example.com"
        )
        assert index_response.status_code == 302
        assert "login" in index_response.url


class TestPlatformHostFlow:
    def test_bootstrap_platform_then_login_and_tenant_admin_writable(
        self, bind_tenant
    ):
        call_command("bootstrap_platform", domain="platform.example.com")
        public_tenant = Tenant.objects.get(schema_name=get_public_schema_name())

        # Superuser: TenantAdmin requires real Django model permissions
        # (add_tenant/view_tenant/...) for ordinary staff — superusers
        # bypass that via ModelBackend.has_perm()'s short-circuit,
        # matching "superuser (public) ... sees /admin/tenant/tenant/".
        staff = UserAccountFactory(
            is_staff=True, is_superuser=True, plain_password="pw12345"
        )
        # Mirrors what TenantMainMiddleware sets on the platform host —
        # connection.tenant is the resolved Tenant row, including for
        # the public schema (django_tenants sets request.tenant/
        # connection.tenant to the actual row regardless of schema).
        bind_tenant(public_tenant)

        client = Client()
        response = _login(
            client,
            username=staff.email,
            password="pw12345",
            server_name="platform.example.com",
        )
        assert response.status_code == 302

        index_response = client.get(
            INDEX_URL, SERVER_NAME="platform.example.com"
        )
        assert index_response.status_code == 200

        # TenantAdmin.has_module_permission() gates on
        # connection.schema_name == "public" — the platform-host
        # surface where Tenant/TenantDomain/UserTenantMembership admin
        # is writable.
        tenant_changelist = reverse("admin:tenant_tenant_changelist")
        changelist_response = client.get(
            tenant_changelist, SERVER_NAME="platform.example.com"
        )
        assert changelist_response.status_code == 200


class TestAdminPasswordChange:
    """``MyAdminSite.password_change`` runs a platform-staff session's
    self-service password change inside ``schema_context(public)`` —
    ``request.user`` is always the public-schema row for such a
    session, regardless of which schema the request's host resolved
    to.
    """

    def test_get_renders_form(self, tenant_a, bind_tenant):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant_a,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )
        bind_tenant(tenant_a)

        client = Client()
        _login(
            client,
            username=staff.email,
            password="pw12345",
            server_name="tenant-a.example.com",
        )
        response = client.get(
            "/admin/password_change/", SERVER_NAME="tenant-a.example.com"
        )
        assert response.status_code == 200

    def test_post_changes_password(self, tenant_a, bind_tenant):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        UserTenantMembership.objects.create(
            user=staff,
            tenant=tenant_a,
            role=TenantMembershipRole.STAFF,
            is_active=True,
        )
        bind_tenant(tenant_a)

        client = Client()
        _login(
            client,
            username=staff.email,
            password="pw12345",
            server_name="tenant-a.example.com",
        )
        response = client.post(
            "/admin/password_change/",
            {
                "old_password": "pw12345",
                "new_password1": "a-brand-new-passw0rd!",
                "new_password2": "a-brand-new-passw0rd!",
            },
            SERVER_NAME="tenant-a.example.com",
        )
        assert response.status_code == 302

        staff.refresh_from_db()
        assert staff.check_password("a-brand-new-passw0rd!")
