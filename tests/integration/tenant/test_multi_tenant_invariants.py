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
class TestMembershipIsolation:
    """A membership in tenant A grants nothing in tenant B (H3).

    ``get_membership`` is what every staff surface consults — the
    admin's ``has_permission`` and ``TenantRolePermissionBackend``
    both resolve roles through it. (Customer Knox tokens need no
    membership check at all: ``knox_authtoken`` is per-schema, so a
    token minted on tenant A does not exist in tenant B's table —
    that isolation is structural, see BoundedTokenAuthentication.)
    """

    def test_membership_does_not_cross_tenants(self) -> None:
        from tenant.membership import get_membership

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

        assert get_membership(user, tenant_a) is not None
        assert get_membership(user, tenant_b) is None

    def test_inactive_membership_does_not_grant_access(self) -> None:
        from tenant.membership import get_membership

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

        assert get_membership(user, tenant) is None


# ---------------------------------------------------------------------------
# Viva webhook schema resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVivaWebhookTenantResolution:
    """``_resolve_tenant_for_order_code`` iterates active tenants and
    finds the schema whose Order table contains the matching
    ``metadata.viva_order_code``.

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

    def test_match_via_viva_order_codes_history_array(self) -> None:
        """A payment on an EARLIER checkout session (stale tab, back
        button) carries an orderCode that only exists in the
        ``metadata['viva_order_codes']`` history array. The resolver
        must match through ``viva_order_code_q`` — matching only the
        latest singular ``viva_order_code`` resolved no tenant, the
        view acked 200, and Viva never retried (payment stranded).
        """
        from order.factories.order import OrderFactory
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        _make_tenant("viva-array-resolver")
        OrderFactory(
            metadata={
                "viva_order_code": "NEWEST-CODE",
                "viva_order_codes": ["STALE-CODE-1", "NEWEST-CODE"],
            }
        )

        # Under the no-op schema_context every active tenant "sees" the
        # public-schema order, so iteration order decides WHICH tenant
        # matches — the regression under test is that the stale array
        # code resolves at all (the old narrow lookup returned None).
        with self._noop_schema_context():
            assert _resolve_tenant_for_order_code("STALE-CODE-1") is not None
            assert _resolve_tenant_for_order_code("NEWEST-CODE") is not None
            assert _resolve_tenant_for_order_code("NEVER-ISSUED") is None


# ---------------------------------------------------------------------------
# BoxNow webhook schema resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoxNowWebhookTenantResolution:
    """``_resolve_tenant_for_parcel`` mirrors the Viva resolver but
    keys on ``BoxNowShipment.parcel_id``.
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
# K8s probe bypass
# ---------------------------------------------------------------------------


class TestHealthProbeBypass:
    """Kubelet probes send the pod IP as Host — a hostname no
    TenantDomain row can ever match. ``HealthProbeMiddleware`` (mounted
    ahead of ``TenantMainMiddleware``) must answer the readiness path
    with 200 regardless of the Host header and without touching any
    backing service.
    """

    def test_health_live_answers_with_bogus_host(self, client):
        # Deliberately NO django_db mark: pytest-django raises on any
        # database access here, so a passing test doubles as proof the
        # probe touches no backing service (a DB outage can't fail it).
        response = client.get(
            "/api/v1/health/live", HTTP_HOST="10.42.0.17:8000"
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# WebSocket group isolation
# ---------------------------------------------------------------------------


class TestWebSocketGroupIsolation:
    """The WebSocket consumer's group name embeds the tenant schema, so
    a notification broadcast on tenant A is delivered only to
    subscribers connected through tenant A's domain. The ticket
    middleware is the auth side; this test pins down the delivery
    side.
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


# ---------------------------------------------------------------------------
# Page-config tenant admin permission
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPageConfigTenantPermission:
    """``PageLayoutAdminViewSet`` must not be reachable by store staff.

    Platform-staff without a membership in
    the current tenant must not mutate that tenant's layout.

    The original guard required ``IsAdminUser`` PAIRED with
    ``HasTenantAccess``. That pairing was unsound on an API request:
    ``UserTenantMembership.user`` is an FK to
    ``public.user_useraccount``, but an API session authenticates
    against the TENANT schema (knox is TENANT_APPS only), so the
    membership lookup compared primary keys ACROSS schemas and matched
    whichever public row shared the pk. It held only because the
    cutover copied users id-preserving.

    H22 is now closed at the root instead: ``is_staff`` is not the gate
    at all. See ``docs/api-staff-identity.md`` for why the API has no
    sound notion of store staff, and what granting it would require.
    """

    def test_admin_viewset_is_role_gated(self) -> None:
        """The end state: permissions derive from the caller's ROLE in
        the tenant on the connection (StoreStaffModelPermissions →
        TenantRolePermissionBackend). An OWNER of store A holds nothing
        on store B's host, which is H22 by construction — and a
        platform superuser still passes via the has_perm short-circuit.

        (An interim pass locked these routes to IsPlatformSuperuser
        while the staff identity did not exist yet; Design B replaced
        that — see docs/api-staff-identity.md.)
        """
        from page_config.views import PageLayoutAdminViewSet

        permission_names = {
            cls.__name__ for cls in PageLayoutAdminViewSet.permission_classes
        }
        assert "StoreStaffModelPermissions" in permission_names

    def test_admin_viewset_does_not_rely_on_is_staff(self) -> None:
        """``IsAdminUser`` is literally ``is_staff`` — the H22 hole."""
        from page_config.views import PageLayoutAdminViewSet

        permission_names = {
            cls.__name__ for cls in PageLayoutAdminViewSet.permission_classes
        }
        assert "IsAdminUser" not in permission_names

    def test_admin_viewset_does_not_match_membership_across_schemas(
        self,
    ) -> None:
        """``HasTenantAccess`` compares pks across schemas on the API.

        Re-adding it here would reintroduce that comparison, so this
        pins its absence rather than leaving it to review.
        """
        from page_config.views import PageLayoutAdminViewSet

        permission_names = {
            cls.__name__ for cls in PageLayoutAdminViewSet.permission_classes
        }
        assert "HasTenantAccess" not in permission_names


# ---------------------------------------------------------------------------
# Cart UUID identifier contract
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCartUuidIdentifier:
    """X-Cart-Id header carries the cart UUID, not the integer PK.
    We exercise the service contract
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
