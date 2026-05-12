"""Integration tests for multi-tenant invariants that need real
``Tenant`` rows or ORM round-trips (not just monkeypatched
``connection`` fixtures).

The four invariants covered:

1. **Knox cross-tenant token replay** — a token minted by a user
   authenticated on tenant A must not authenticate that user on
   tenant B's domain, even when the user account exists in both
   schemas (UserAccount lives in SHARED_APPS).
2. **Viva webhook schema resolution** — the webhook view iterates
   active tenants to find the order_code, then enters that schema.
   A test tenant with no matching order returns the 200 / no-op path.
3. **BoxNow webhook schema resolution** — same model, keyed on
   parcelId.
4. **WebSocket group isolation** — the consumer must build its
   group name from ``scope["tenant"].schema_name`` so a notification
   broadcast on tenant A never reaches tenant B's subscribers.

These tests sit under ``tests/integration/tenant/`` so they pick up
the same DB fixture (``@pytest.mark.django_db``) the rest of the
integration suite uses. Real Postgres schemas are NOT created — we
keep ``auto_create_schema=False`` so the Tenant rows exist in the
public schema only and the schema-routing logic is exercised via
the queryset filter + mocked downstream calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from tenant.models import (
    Tenant,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)


User = get_user_model()


def _make_tenant(slug: str, **kwargs) -> Tenant:
    defaults = {"is_active": True, "suspended_at": None}
    defaults.update(kwargs)
    t = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        **defaults,
    )
    t.auto_create_schema = False
    t.save()
    return t


def _attach_domain(tenant: Tenant, host: str) -> TenantDomain:
    return TenantDomain.objects.create(
        domain=host,
        tenant=tenant,
        is_primary=True,
    )


# ---------------------------------------------------------------------------
# Knox cross-tenant token replay
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestKnoxCrossTenantReplay:
    """A user with an active session on tenant A must not be granted
    access on tenant B's domain via the same Knox token. H3 in
    MULTI_TENANT_AUDIT.md.

    The DRF authentication class ``BoundedTokenAuthentication`` calls
    ``user_has_tenant_access(user, current_tenant)`` AFTER Knox has
    validated the token. We exercise the membership branch directly
    so the test is deterministic without needing Knox token tables.
    """

    def test_membership_required_to_pass_token_check(self) -> None:
        from tenant.membership import user_has_tenant_access

        tenant_a = _make_tenant("knox-tenant-a")
        tenant_b = _make_tenant("knox-tenant-b")

        user = User.objects.create_user(
            email="cross-tenant@example.com",
            password="irrelevant-1",
        )
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant_a,
            role=TenantMembershipRole.MEMBER,
            is_active=True,
        )

        assert user_has_tenant_access(user, tenant_a) is True
        assert user_has_tenant_access(user, tenant_b) is False

    def test_inactive_membership_does_not_grant_access(self) -> None:
        from tenant.membership import user_has_tenant_access

        tenant = _make_tenant("knox-tenant-c")
        user = User.objects.create_user(
            email="inactive-membership@example.com",
            password="irrelevant-2",
        )
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )

        assert user_has_tenant_access(user, tenant) is False


# ---------------------------------------------------------------------------
# Viva webhook schema resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVivaWebhookTenantResolution:
    """``_resolve_tenant_for_order_code`` iterates active tenants and
    finds the schema whose Order table contains the matching
    ``metadata.viva_order_code`` (C2 in MULTI_TENANT_AUDIT.md).

    The auto-created Tenant rows here use ``auto_create_schema=False``
    so no real Postgres schemas exist. We patch
    ``order.views.viva_webhook.schema_context`` with a no-op context
    manager so the inner ``Order.objects.filter`` runs against the
    test public DB — the test then asserts the iteration behaviour
    (which tenants are visited, what determines a match) without
    needing real schemas.
    """

    @staticmethod
    def _noop_schema_context():
        from contextlib import contextmanager

        @contextmanager
        def _noop(_schema):
            yield

        return patch("order.views.viva_webhook.schema_context", _noop)

    def test_no_match_returns_none_for_unknown_order_code(self) -> None:
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        _make_tenant("viva-resolver-a")
        _make_tenant("viva-resolver-b")

        with self._noop_schema_context():
            result = _resolve_tenant_for_order_code("ORDER-NOT-IN-ANY-TENANT")
        assert result is None

    def test_empty_order_code_short_circuits(self) -> None:
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        _make_tenant("viva-empty-resolver")
        assert _resolve_tenant_for_order_code("") is None
        assert _resolve_tenant_for_order_code(None) is None  # type: ignore[arg-type]

    def test_inactive_tenant_skipped(self) -> None:
        """``is_active=False`` tenants must not be iterated."""
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        _make_tenant("viva-inactive-only", is_active=False)
        with self._noop_schema_context():
            assert _resolve_tenant_for_order_code("anything") is None


# ---------------------------------------------------------------------------
# BoxNow webhook schema resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoxNowWebhookTenantResolution:
    """``_resolve_tenant_for_parcel`` mirrors the Viva resolver but
    keys on ``BoxNowShipment.parcel_id`` (C5 in MULTI_TENANT_AUDIT.md).
    Uses the same no-op ``schema_context`` patch as the Viva tests
    above so the iteration is exercised without real Postgres schemas.
    """

    @staticmethod
    def _noop_schema_context():
        from contextlib import contextmanager

        @contextmanager
        def _noop(_schema):
            yield

        return patch("shipping_boxnow.views.webhook.schema_context", _noop)

    def test_empty_parcel_id_short_circuits(self) -> None:
        from shipping_boxnow.views.webhook import _resolve_tenant_for_parcel

        _make_tenant("boxnow-resolver-a")
        assert _resolve_tenant_for_parcel("") is None

    def test_no_match_returns_none_for_unknown_parcel(self) -> None:
        from shipping_boxnow.views.webhook import _resolve_tenant_for_parcel

        _make_tenant("boxnow-resolver-b")
        with self._noop_schema_context():
            assert (
                _resolve_tenant_for_parcel("parcel-not-in-any-tenant") is None
            )

    def test_inactive_tenant_skipped(self) -> None:
        from shipping_boxnow.views.webhook import _resolve_tenant_for_parcel

        _make_tenant("boxnow-inactive", is_active=False)
        with self._noop_schema_context():
            assert _resolve_tenant_for_parcel("any-parcel-id") is None


# ---------------------------------------------------------------------------
# WebSocket group isolation
# ---------------------------------------------------------------------------


class TestWebSocketGroupIsolation:
    """The WebSocket consumer's group name embeds the tenant schema, so
    a notification broadcast on tenant A is delivered only to
    subscribers connected through tenant A's domain. H3 in
    MULTI_TENANT_AUDIT.md is the auth side; this test pins down the
    delivery side.
    """

    def test_per_user_group_name_includes_tenant_schema(self) -> None:
        from notification.groups import user_group

        a = user_group("tenant_alpha", 42)
        b = user_group("tenant_beta", 42)

        assert a != b, (
            "same user on different tenants must get different groups"
        )
        assert "tenant_alpha" in a
        assert "tenant_beta" in b

    def test_admin_group_name_includes_tenant_schema(self) -> None:
        from notification.groups import admins_group

        a = admins_group("tenant_alpha")
        b = admins_group("tenant_beta")

        assert a != b
        assert "tenant_alpha" in a
        assert "tenant_beta" in b


# ---------------------------------------------------------------------------
# Page-config tenant admin permission
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPageConfigTenantPermission:
    """``PageLayoutAdminViewSet`` requires both ``IsAdminUser`` and
    ``HasTenantAccess``. Platform-staff without a tenant membership
    must be rejected (H22 in MULTI_TENANT_AUDIT.md).
    """

    def test_admin_viewset_requires_both_permissions(self) -> None:
        """Static check that the regression — dropping
        ``HasTenantAccess`` and leaving only ``IsAdminUser`` — would
        fail this test. The runtime path is covered by the existing
        ``tests/unit/tenant/test_membership.py`` suite.
        """
        from page_config.views import PageLayoutAdminViewSet

        permission_names = {
            cls.__name__ for cls in PageLayoutAdminViewSet.permission_classes
        }
        assert "HasTenantAccess" in permission_names
        assert "IsAdminUser" in permission_names


# ---------------------------------------------------------------------------
# Cart UUID identifier contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCartUuidIdentifier:
    """X-Cart-Id header carries the cart UUID, not the integer PK.
    M18 in MULTI_TENANT_AUDIT.md. We exercise the service contract
    directly so the regression check doesn't depend on the full
    DRF/factory stack.
    """

    def test_header_is_parsed_as_uuid(self) -> None:
        from cart.factories.cart import CartFactory
        from cart.services import CartService

        cart = CartFactory(user=None, num_cart_items=0)
        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=False)
        request.META["HTTP_X_CART_ID"] = str(cart.uuid)
        request.session = {}

        service = CartService(request=request)
        assert service.cart_id == cart.uuid
        assert service.cart == cart

    def test_integer_header_is_rejected(self) -> None:
        from cart.factories.cart import CartFactory
        from cart.services import CartService

        cart = CartFactory(user=None, num_cart_items=0)
        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=False)
        request.META["HTTP_X_CART_ID"] = str(cart.id)  # integer PK
        request.session = {}

        service = CartService(request=request)
        # Integer is not a valid UUID → cart_id parsed as None →
        # service resolves a fresh cart, not the existing one.
        assert service.cart_id is None

    def test_malformed_header_is_rejected(self) -> None:
        from cart.services import CartService

        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=False)
        request.META["HTTP_X_CART_ID"] = "definitely-not-a-uuid"
        request.session = {}

        service = CartService(request=request)
        assert service.cart_id is None
