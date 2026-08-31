from unittest.mock import patch

import pytest
from django.db import connection

from b2b.vies import ViesResult
from tenant.models import Tenant


@pytest.fixture
def b2b_tenant(db, monkeypatch):
    """A tenant row with the B2B plan flag on, active for the request."""
    tenant = Tenant(
        schema_name="b2b_gate_tenant",
        name="b2b-gate-tenant",
        slug="b2b-gate-tenant",
        owner_email="owner-b2b@example.com",
        b2b_enabled=True,
    )
    tenant.auto_create_schema = False
    tenant.save()
    monkeypatch.setattr(connection, "tenant", tenant, raising=False)
    return tenant


@pytest.fixture
def enable_wholesale():
    """Runtime half of the gate — B2B_WHOLESALE_ENABLED on."""

    def _get(key, default=None):
        return {"B2B_WHOLESALE_ENABLED": True}.get(key, default)

    with (
        patch("extra_settings.models.Setting.get", side_effect=_get),
        patch("b2b.services.Setting.get", side_effect=_get),
    ):
        yield


@pytest.fixture
def mock_vies():
    with patch(
        "b2b.services.ViesClient.check_vat",
        return_value=ViesResult(
            valid=True, name="EXAMPLE IKE", address="ERMOU 1, ATHENS"
        ),
    ) as mocked:
        yield mocked
