"""Credential helpers only work under ``tenant_context``, never
``schema_context``.

``tenant.credentials._get_tenant_field`` reads real columns off
``connection.tenant``. ``schema_context(schema_name)`` sets that to a
django-tenants ``FakeTenant``, which carries ONLY ``schema_name`` — so
every per-merchant credential reads as "" and the caller concludes the
integration is unconfigured. Nothing raises, and since the settings
fallbacks were deleted there is no longer anything to mask it.

This already bit ``TenantTask`` and both payment webhooks. It was still
live in ``bootstrap_stripe``, which §0.3 of the cutover runbook tells
the operator to run IMMEDIATELY AFTER backfilling the Stripe key — it
would have read the freshly-written key as empty, skipped provisioning
the per-schema WebhookEndpoint, and reported success.
"""

from __future__ import annotations

from django_tenants.utils import get_tenant_model  # noqa: F401

from tenant.credentials import _get_tenant_field
from tests.utils.staff import store_tenant


class _FakeTenant:
    """Stand-in matching django-tenants' FakeTenant surface.

    django-tenants sets exactly this shape on ``connection.tenant``
    inside ``schema_context`` — a schema name and nothing else.
    """

    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def test_a_tenant_without_the_field_reads_as_unconfigured(bind_tenant):
    bind_tenant(_FakeTenant("webside"))
    # Not an error — just silently empty, which is the whole problem.
    assert _get_tenant_field("store_name") == ""
    assert _get_tenant_field("stripe_secret_key") == ""


def test_real_tenant_row_reads_the_value(db, bind_tenant):
    tenant = store_tenant("creds_probe")
    tenant.store_name = "Probe Store"
    bind_tenant(tenant)
    assert _get_tenant_field("store_name") == "Probe Store"
