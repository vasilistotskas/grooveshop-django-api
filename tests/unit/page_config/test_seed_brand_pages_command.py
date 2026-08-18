"""Tests for the ``seed_brand_pages`` management command.

Opt-in counterpart to the universal ``seed_page_layouts`` (run for
every tenant during ``tenant_create``). No tenant name is hardcoded in
the command — this only pins the ``--schema`` guard and that it wires
into ``page_config.defaults.seed_brand_pages``.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from page_config.defaults import BRAND_PAGE_LAYOUTS
from page_config.models import PageLayout
from tenant.models import Tenant

pytestmark = pytest.mark.django_db


def test_raises_when_tenant_does_not_exist():
    with pytest.raises(CommandError, match="No tenant with schema"):
        call_command("seed_brand_pages", schema="does-not-exist")


def test_seeds_layouts_for_existing_tenant():
    # ``schema_name="public"`` bypasses ``Tenant.full_clean()``'s
    # reserved-name validator the same way ``tenant_create`` documents
    # bypassing it via a plain ``.save()`` — safe here since the test
    # DB has no real per-schema separation (DATABASE_ROUTERS is empty
    # in tests), so ``schema_context("public")`` is a same-schema
    # round-trip.
    tenant = Tenant(
        schema_name="public",
        name="Seed Brand Pages Test",
        slug="seed-brand-pages-test",
        owner_email="owner-seed-brand-pages@example.com",
    )
    tenant.auto_create_schema = False
    tenant.save()

    call_command("seed_brand_pages", schema="public")

    for page_type in BRAND_PAGE_LAYOUTS:
        assert PageLayout.objects.filter(page_type=page_type).exists()
