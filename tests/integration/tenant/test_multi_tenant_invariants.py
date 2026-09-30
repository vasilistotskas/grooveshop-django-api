"""Integration tests for multi-tenant invariants that need real
``Tenant`` rows or ORM round-trips (not just monkeypatched
``connection`` fixtures).

Covered here:

1. **Membership isolation** — a role in tenant A grants nothing in B.
2. **Viva / BoxNow webhook schema resolution** — the webhook views
   iterate ACTIVE tenants to find the schema owning the order code /
   parcel id. The main lane has no real tenant schemas, so
   ``schema_context`` is patched to a no-op and every tenant "sees" the
   public-schema rows; the tests pin which tenants are visited.
3. **Store-scoped admin routes are role-gated per tenant** — an OWNER
   of store A holds nothing on store B's host.
4. **Cart UUID identifier contract** and the **health probe bypass**.

Knox cross-tenant token replay needs real per-tenant ``knox_authtoken``
tables and so belongs in the multi-tenant lane (``tests_mt``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.test import APIClient

from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_staff, store_tenant

User = get_user_model()


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

        tenant_a = store_tenant("knox_tenant_a")
        tenant_b = store_tenant("knox_tenant_b")

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


# ---------------------------------------------------------------------------
# Viva webhook schema resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVivaWebhookTenantResolution:
    """``_resolve_tenant_for_order_code`` iterates active tenants and
    finds the schema whose Order table contains the matching
    ``metadata.viva_order_code``.

    No real tenant schemas exist in this lane. We patch
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

        with self._noop_schema_context():
            result = _resolve_tenant_for_order_code("ORDER-NOT-IN-ANY-TENANT")
        assert result is None

    def test_empty_order_code_short_circuits(self) -> None:
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        assert _resolve_tenant_for_order_code("") is None
        assert _resolve_tenant_for_order_code(None) is None  # type: ignore[arg-type]

    def test_inactive_tenant_skipped(self) -> None:
        """``is_active=False`` tenants must not be iterated."""
        from order.factories.order import OrderFactory
        from order.views.viva_webhook import _resolve_tenant_for_order_code

        OrderFactory(
            metadata={"viva_order_codes": ["INACTIVE-CODE"]},
            num_order_items=0,
        )
        with self._noop_schema_context():
            assert _resolve_tenant_for_order_code("INACTIVE-CODE") is not None
            Tenant.objects.update(is_active=False)
            assert _resolve_tenant_for_order_code("INACTIVE-CODE") is None

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
    above.
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

        assert _resolve_tenant_for_parcel("") is None

    def test_no_match_returns_none_for_unknown_parcel(self) -> None:
        from shipping_boxnow.views.webhook import _resolve_tenant_for_parcel

        with self._noop_schema_context():
            assert (
                _resolve_tenant_for_parcel("parcel-not-in-any-tenant") is None
            )

    def test_inactive_tenant_skipped(self) -> None:
        from shipping_boxnow.factories import BoxNowShipmentFactory
        from shipping_boxnow.views.webhook import _resolve_tenant_for_parcel

        BoxNowShipmentFactory(parcel_id="9200000001")
        with self._noop_schema_context():
            assert _resolve_tenant_for_parcel("9200000001") is not None
            Tenant.objects.update(is_active=False)
            assert _resolve_tenant_for_parcel("9200000001") is None


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
# Store-scoped admin routes
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_page_layout_admin_refuses_the_owner_of_another_store(bind_tenant):
    """H22: permissions derive from the caller's ROLE in the tenant on
    the connection (``StoreStaffModelPermissions`` →
    ``TenantRolePermissionBackend``), never from ``is_staff``. See
    ``docs/api-staff-identity.md``."""
    store_a = store_tenant("layout_store_a")
    store_b = store_tenant("layout_store_b")
    client = APIClient()
    client.force_authenticate(
        user=store_staff(store_a, role=TenantMembershipRole.OWNER)
    )

    bind_tenant(store_a)
    assert client.get("/api/v1/page-config/admin").status_code == 200
    bind_tenant(store_b)
    assert client.get("/api/v1/page-config/admin").status_code == 403


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
