"""Staff token issuance and the role-derived API permission layer.

Issuance: only on the platform URLconf, only for platform identities
that can operate something. Authorization: DRF's method→codename
mapping resolving through ``TenantRolePermissionBackend``, so the API
answers "what may a STAFF do" from the same policy as the admin.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import RequestFactory
from rest_framework.test import APIClient

from core.api.permissions import (
    StoreStaffChangePermission,
    StoreStaffModelPermissions,
)
from tenant.api_tokens import PlatformStaffTokenAuthentication
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import (
    PlatformStaffToken,
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory
from user.models import UserAccount

LOGIN = "/api/v1/platform/auth/login"
LOGOUT = "/api/v1/platform/auth/logout"
PASSWORD = "staff-api-pass-123"


@pytest.fixture
def tenant(db):
    t = Tenant(
        schema_name="staffapi_tenant",
        name="Staffapi Tenant",
        slug="staffapi-tenant",
        owner_email="owner-staffapi@example.com",
    )
    t.auto_create_schema = False
    t.save()
    return t


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    return _bind


def _staff(*, tenant=None, role=TenantMembershipRole.OWNER, **kwargs):
    user = UserAccount.objects.create_user(
        email=kwargs.pop("email", "staff-api@example.com"),
        username=kwargs.pop("username", "staffapiuser"),
        password=PASSWORD,
        **kwargs,
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    if tenant is not None:
        UserTenantMembership.objects.create(
            user=user, tenant=tenant, role=role, is_active=True
        )
    return user


@pytest.mark.urls("tenant.urls_public")
@pytest.mark.django_db
class TestStaffLogin:
    def test_mints_a_working_token(self, tenant):
        user = _staff(tenant=tenant)
        response = APIClient().post(
            LOGIN, {"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.data
        token = response.data["token"]

        request = RequestFactory().get("/")
        request.META["HTTP_AUTHORIZATION"] = f"StaffBearer {token}"
        auth_user, _ = PlatformStaffTokenAuthentication().authenticate(request)
        assert auth_user == user

    def test_superuser_needs_no_membership(self):
        user = _staff(email="su-staffapi@example.com", username="sustaffapi")
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
        response = APIClient().post(
            LOGIN, {"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 200

    def test_staff_without_any_membership_is_refused(self):
        """A token that could grant nothing is not minted at all."""
        user = _staff(email="idle-staff@example.com", username="idlestaff")
        response = APIClient().post(
            LOGIN, {"email": user.email, "password": PASSWORD}
        )
        assert response.status_code == 403

    def test_a_customer_cannot_mint(self, tenant):
        """No is_staff → authenticate_staff itself refuses."""
        customer = UserAccount.objects.create_user(
            email="customer-staffapi@example.com",
            username="customerstaffapi",
            password=PASSWORD,
        )
        response = APIClient().post(
            LOGIN, {"email": customer.email, "password": PASSWORD}
        )
        assert response.status_code == 401

    def test_wrong_password_is_refused(self, tenant):
        user = _staff(tenant=tenant)
        response = APIClient().post(
            LOGIN, {"email": user.email, "password": "wrong"}
        )
        assert response.status_code == 401

    def test_logout_revokes_the_presented_token(self, tenant):
        user = _staff(tenant=tenant)
        _, token = PlatformStaffToken.objects.create(user)

        response = APIClient().post(
            LOGOUT, HTTP_AUTHORIZATION=f"StaffBearer {token}"
        )
        assert response.status_code == 204
        assert PlatformStaffToken.objects.filter(user=user).count() == 0

    def test_the_storefront_urlconf_has_no_login_route(self):
        """Half of the minting wall: core.urls never mounts these."""
        from django.urls import Resolver404, resolve

        with pytest.raises(Resolver404):
            resolve(LOGIN, urlconf="core.urls")


@pytest.mark.django_db
class TestStoreStaffModelPermissions:
    """The permission layer, exercised through real ``user.has_perm``."""

    def _check(self, user, method="POST", model=None):
        from product.models.product import Product

        view = type(
            "View", (), {"queryset": (model or Product).objects.all()}
        )()
        request = RequestFactory().generic(method, "/")
        request.user = user
        return StoreStaffModelPermissions().has_permission(request, view)

    def _operator(self, tenant, role):
        user = UserAccountFactory(is_staff=True)
        setattr(user, PLATFORM_IDENTITY_ATTR, True)
        UserTenantMembership.objects.create(
            user=user, tenant=tenant, role=role, is_active=True
        )
        return user

    def test_owner_may_create(self, tenant, bind_tenant):
        bind_tenant(tenant)
        user = self._operator(tenant, TenantMembershipRole.OWNER)
        assert self._check(user, "POST") is True

    def test_staff_may_change_but_not_delete(self, tenant, bind_tenant):
        bind_tenant(tenant)
        user = self._operator(tenant, TenantMembershipRole.STAFF)
        assert self._check(user, "PUT") is True
        assert self._check(user, "DELETE") is False

    def test_admin_may_delete(self, tenant, bind_tenant):
        bind_tenant(tenant)
        user = self._operator(tenant, TenantMembershipRole.ADMIN)
        assert self._check(user, "DELETE") is True

    def test_reads_require_the_view_permission(self, tenant, bind_tenant):
        """Administrative reads are staff activity, not public ones."""
        bind_tenant(tenant)
        user = self._operator(tenant, TenantMembershipRole.STAFF)
        assert self._check(user, "GET") is True

        stranger = UserAccountFactory(is_staff=True)
        setattr(stranger, PLATFORM_IDENTITY_ATTR, True)
        assert self._check(stranger, "GET") is False

    def test_an_unstamped_identity_holds_nothing(self, tenant, bind_tenant):
        """THE collision defence, at the API layer: a tenant-schema
        customer sharing a pk with a member grants itself nothing,
        because only PlatformStaffTokenAuthentication stamps."""
        bind_tenant(tenant)
        user = UserAccountFactory(is_staff=True)  # deliberately unstamped
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.OWNER,
            is_active=True,
        )
        assert self._check(user, "POST") is False

    def test_role_does_not_cross_tenants(self, tenant, bind_tenant):
        other = Tenant(
            schema_name="staffapi_other",
            name="Staffapi Other",
            slug="staffapi-other",
            owner_email="owner-staffapi-other@example.com",
        )
        other.auto_create_schema = False
        other.save()

        user = self._operator(tenant, TenantMembershipRole.OWNER)
        bind_tenant(other)
        assert self._check(user, "POST") is False

    def test_change_permission_class_maps_custom_actions(
        self, tenant, bind_tenant
    ):
        """POST custom actions carry change semantics, not add — a
        STAFF may refund/cancel (change_order) even though delete is
        withheld."""
        from order.models.order import Order

        bind_tenant(tenant)
        user = self._operator(tenant, TenantMembershipRole.STAFF)
        view = type("View", (), {"queryset": Order.objects.all()})()
        request = RequestFactory().post("/")
        request.user = user
        assert (
            StoreStaffChangePermission().has_permission(request, view) is True
        )
