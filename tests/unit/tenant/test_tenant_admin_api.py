"""The platform tenant-admin API cannot skip the destroy gates or create
tenants around provisioning."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from tenant.lifecycle import SUSPEND_COOLDOWN
from tenant.models import Tenant
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("tenant.urls_public")]


def _tenant(schema_name: str, **kwargs) -> Tenant:
    tenant = Tenant(
        schema_name=schema_name,
        name=schema_name,
        slug=schema_name.replace("_", "-"),
        owner_email=f"owner-{schema_name}@example.com",
        **kwargs,
    )
    tenant.auto_create_schema = False
    tenant.save()
    return tenant


@pytest.fixture
def operator_client():
    """A platform superuser whose session came from PlatformStaffBackend."""
    client = APIClient()
    client.force_authenticate(
        user=UserAccountFactory(is_staff=True, is_superuser=True)
    )
    with patch(
        "tenant.auth_backends.is_platform_staff_session", return_value=True
    ):
        yield client


@pytest.fixture
def no_destroy_side_effects():
    with (
        patch.object(Tenant, "auto_create_schema", False),
        patch("tenant.offboarding.latest_invoice_year", return_value=None),
        patch("tenant.offboarding.purge_search_indexes", return_value=0),
        patch("tenant.offboarding.purge_tenant_files", return_value={}),
        patch("tenant.lifecycle._dispatch_media_flush"),
        patch("tenant.lifecycle.has_tenant_export", return_value=False),
        patch.object(Tenant, "delete") as delete,
    ):
        yield delete


def test_destroying_a_live_tenant_is_refused(
    operator_client, no_destroy_side_effects
):
    tenant = _tenant("api_live", is_active=True)
    response = operator_client.delete(
        reverse("tenant-admin-detail", args=[tenant.pk])
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "suspended first" in response.data["detail"]
    no_destroy_side_effects.assert_not_called()


def test_destroying_inside_the_cooldown_is_refused(
    operator_client, no_destroy_side_effects
):
    tenant = _tenant(
        "api_recent",
        is_active=False,
        suspended_at=timezone.now() - timedelta(hours=1),
    )
    response = operator_client.delete(
        reverse("tenant-admin-detail", args=[tenant.pk])
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    no_destroy_side_effects.assert_not_called()


def test_destroying_a_protected_tenant_is_refused(
    operator_client, no_destroy_side_effects
):
    tenant = _tenant(
        "api_protected",
        is_active=False,
        is_protected=True,
        suspended_at=timezone.now() - SUSPEND_COOLDOWN - timedelta(hours=1),
    )
    response = operator_client.delete(
        reverse("tenant-admin-detail", args=[tenant.pk])
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "protected" in response.data["detail"]
    no_destroy_side_effects.assert_not_called()


def test_suspended_past_cooldown_is_destroyed(
    operator_client, no_destroy_side_effects
):
    tenant = _tenant(
        "api_destroyable",
        is_active=False,
        suspended_at=timezone.now() - SUSPEND_COOLDOWN - timedelta(hours=1),
    )
    response = operator_client.delete(
        reverse("tenant-admin-detail", args=[tenant.pk])
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["schemaName"] == "api_destroyable"
    no_destroy_side_effects.assert_called_once_with(force_drop=True)


def test_tenants_are_not_created_through_the_api(operator_client):
    response = operator_client.post(
        reverse("tenant-admin-list"),
        {"name": "Rogue", "slug": "rogue", "ownerEmail": "r@example.com"},
        format="json",
    )
    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert not Tenant.objects.filter(slug="rogue").exists()


# ---------------------------------------------------------------------------
# Lifecycle state is not a settings field
# ---------------------------------------------------------------------------


class TestIsActiveCannotBeWrittenThroughTheSerializer:
    """``PATCH {"isActive": ...}`` skipped every gate lifecycle enforces.

    Measured against this endpoint before the fix:

    * ``false`` -> 200, ``suspended_at`` left NULL and no media flush, so
      the store kept serving processed images for the cache TTL (up to
      360 days) and ``destroy_refusal`` answered ``"not_suspended"`` —
      a store suspended that way could never be destroyed through the
      gated path.
    * ``true`` -> 200 leaving ``suspended_at`` and ``suspended_reason``
      in place, so the NEXT genuine suspension kept the stale anchor: a
      freshly suspended store reported a 30-day-old anchor and a
      ``destroy_refusal`` of ``None``. The 24h cooldown that makes a
      mistaken suspension reversible was already spent.
    * a PROTECTED tenant flipped to inactive, which both lifecycle
      functions refuse.
    """

    def test_patching_is_active_is_ignored(self, operator_client):
        tenant = _tenant("api_patch_active", is_active=True)

        response = operator_client.patch(
            reverse("tenant-admin-detail", args=[tenant.pk]),
            {"isActive": False},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.is_active is True, "the field is still writable"

    def test_patching_a_protected_tenant_is_ignored(self, operator_client):
        tenant = _tenant(
            "api_patch_protected", is_active=True, is_protected=True
        )

        operator_client.patch(
            reverse("tenant-admin-detail", args=[tenant.pk]),
            {"isActive": False},
            format="json",
        )

        tenant.refresh_from_db()
        assert tenant.is_active is True


class TestTheLifecycleRoutes:
    def test_suspend_records_the_anchor_and_flushes_media(
        self, operator_client
    ):
        tenant = _tenant("api_suspend", is_active=True)

        with patch("tenant.lifecycle._dispatch_media_flush") as flush:
            response = operator_client.post(
                reverse("tenant-admin-suspend", args=[tenant.pk]),
                {"reason": "non-payment"},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["changed"] is True
        tenant.refresh_from_db()
        assert tenant.is_active is False
        assert tenant.suspended_at is not None, "no destroy-cooldown anchor"
        assert tenant.suspended_reason == "non-payment"
        flush.assert_called_once_with(tenant.schema_name)

    def test_suspend_requires_a_reason(self, operator_client):
        tenant = _tenant("api_suspend_noreason", is_active=True)

        response = operator_client.post(
            reverse("tenant-admin-suspend", args=[tenant.pk]),
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        tenant.refresh_from_db()
        assert tenant.is_active is True

    def test_suspend_refuses_a_protected_tenant(self, operator_client):
        tenant = _tenant(
            "api_suspend_protected", is_active=True, is_protected=True
        )

        response = operator_client.post(
            reverse("tenant-admin-suspend", args=[tenant.pk]),
            {"reason": "whatever"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        tenant.refresh_from_db()
        assert tenant.is_active is True

    def test_activate_clears_the_anchor_with_the_flag(self, operator_client):
        """Leaving the anchor is what spent the cooldown in advance."""
        tenant = _tenant(
            "api_activate",
            is_active=False,
            suspended_at=timezone.now() - timedelta(days=30),
            suspended_reason="abuse",
        )

        response = operator_client.post(
            reverse("tenant-admin-activate", args=[tenant.pk]), format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.is_active is True
        assert tenant.suspended_at is None
        assert tenant.suspended_reason == ""

    def test_a_reactivated_store_gets_a_fresh_cooldown(self, operator_client):
        """The end-to-end consequence, not just the field values."""
        from tenant.lifecycle import destroy_refusal, suspend_tenant

        tenant = _tenant(
            "api_fresh_cooldown",
            is_active=False,
            suspended_at=timezone.now() - timedelta(days=30),
            suspended_reason="abuse",
        )
        operator_client.post(
            reverse("tenant-admin-activate", args=[tenant.pk]), format="json"
        )
        tenant.refresh_from_db()

        with patch("tenant.lifecycle._dispatch_media_flush"):
            suspend_tenant(tenant, reason="fresh")
        tenant.refresh_from_db()

        assert destroy_refusal(tenant) == "cooldown", (
            "the stale anchor let the cooldown elapse before the suspension"
        )
