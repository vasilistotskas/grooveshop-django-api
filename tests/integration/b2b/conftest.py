from unittest.mock import patch

import pytest

from b2b.vies import ViesResult
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)


@pytest.fixture
def b2b_tenant(db):
    """A tenant row with the B2B plan flag on, bound for the request.

    Bound through ``connection.set_tenant`` rather than by assigning
    ``connection.tenant``: submitting a profile runs the merchant
    notification task eagerly, which enters ``schema_context("public")``
    and on exit restores the connection with ``set_tenant(previous)``.
    With a bare attribute assignment that exit rewrote
    ``connection.schema_name`` to this tenant while the attribute
    restore left it there, and the next ``Tenant.save()`` in the worker
    refused to run "outside the public schema".
    """
    tenant = store_tenant(
        "b2b_gate_tenant",
        name="b2b-gate-tenant",
        owner_email="owner-b2b@example.com",
        b2b_enabled=True,
    )
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


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
