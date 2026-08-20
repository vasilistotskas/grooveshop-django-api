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

import pathlib
import re

from django_tenants.utils import get_tenant_model  # noqa: F401

from tenant.credentials import _get_tenant_field


class _FakeTenant:
    """Stand-in matching django-tenants' FakeTenant surface.

    django-tenants sets exactly this shape on ``connection.tenant``
    inside ``schema_context`` — a schema name and nothing else.
    """

    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def test_fake_tenant_reads_every_field_as_unconfigured(bind_tenant):
    bind_tenant(_FakeTenant("webside"))
    # Not an error — just silently empty, which is the whole problem.
    assert _get_tenant_field("store_name") == ""
    assert _get_tenant_field("stripe_secret_key") == ""


def test_real_tenant_row_reads_the_value(tenant_factory, bind_tenant):
    tenant = tenant_factory("creds-probe")
    tenant.store_name = "Probe Store"
    bind_tenant(tenant)
    assert _get_tenant_field("store_name") == "Probe Store"


HELPERS = re.compile(
    r"(credentials\(\)|tenant_from_email|tenant_site_name"
    r"|tenant_contact_email|tenant_logo_url|tenant_totp_issuer"
    r"|tenant_meta_|_get_tenant_field)"
)


def test_no_credential_helper_is_called_inside_schema_context():
    """Source-level: the runtime failure is silent, so a behavioural
    test would have to assert the absence of something at every call
    site of every helper."""
    root = pathlib.Path(__file__).resolve().parents[3]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        posix = path.as_posix()
        if any(
            skip in posix
            for skip in (".venv", "/tests/", "/migrations/", "/node_modules/")
        ):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        for i, line in enumerate(lines):
            if "with schema_context(" not in line:
                continue
            indent = len(line) - len(line.lstrip())
            body: list[str] = []
            for nxt in lines[i + 1 : i + 40]:
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                body.append(nxt)
            # Comments explain the trap and legitimately name the helpers.
            code = "\n".join(
                ln for ln in body if not ln.strip().startswith("#")
            )
            if HELPERS.search(code):
                offenders.append(f"{path.relative_to(root).as_posix()}:{i + 1}")

    assert not offenders, (
        "credential helper called inside schema_context (reads as "
        "unconfigured — use tenant_context(tenant)): " + ", ".join(offenders)
    )
