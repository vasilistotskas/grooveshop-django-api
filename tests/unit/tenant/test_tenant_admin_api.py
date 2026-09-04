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
