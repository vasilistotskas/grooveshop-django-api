"""Guard rails of the post-cutover public-schema prune command."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_tenants.utils import schema_context

from product.models.product import Product
from tenant.models import Tenant, TenantDomain


def _make_active_tenant(slug: str) -> Tenant:
    tenant = Tenant(
        schema_name="public",
        name=f"Prune {slug}",
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
    )
    tenant.auto_create_schema = False
    tenant.save()
    # The command excludes schema_name="public" from the active-tenant
    # check, so give it a non-public row via direct update (bypasses the
    # reserved-name validator the same way tenant_create documents).
    Tenant.objects.filter(pk=tenant.pk).update(schema_name=f"prune_{slug}")
    tenant.refresh_from_db()
    TenantDomain.objects.create(
        tenant=tenant, domain=f"{slug}.example", is_primary=True
    )
    return tenant


@pytest.mark.django_db(transaction=True)
class TestPrunePublicLegacyData:
    def test_refuses_without_active_tenants(self):
        # Data migrations may seed tenant rows in the test DB — clear
        # them so the guard actually triggers.
        Tenant.objects.all().delete()
        with (
            schema_context("public"),
            pytest.raises(CommandError, match="No active tenant schemas"),
        ):
            call_command("prune_public_legacy_data", "--dry-run")

    def test_refuses_outside_public_schema(self):
        _make_active_tenant("prune-ctx")
        with (
            schema_context("prune_prune-ctx"),
            pytest.raises(CommandError, match="public schema context"),
        ):
            call_command("prune_public_legacy_data", "--dry-run")

    def test_refuses_destructive_run_without_yes(self):
        _make_active_tenant("prune-yes")
        with (
            schema_context("public"),
            pytest.raises(CommandError, match="--yes"),
        ):
            call_command("prune_public_legacy_data")

    def test_dry_run_reports_and_deletes_nothing(
        self, capsys, product_factory=None
    ):
        _make_active_tenant("prune-dry")
        from product.factories.product import ProductFactory

        ProductFactory(num_images=0, num_reviews=0)
        before = Product.objects.count()
        assert before > 0

        with schema_context("public"):
            call_command("prune_public_legacy_data", "--dry-run")

        assert Product.objects.count() == before

    def test_yes_truncates_tenant_only_tables(self):
        _make_active_tenant("prune-go")
        from product.factories.product import ProductFactory

        ProductFactory(num_images=0, num_reviews=0)
        assert Product.objects.count() > 0
        users_before = None
        from django.contrib.auth import get_user_model

        users_before = get_user_model().objects.count()

        with schema_context("public"):
            call_command("prune_public_legacy_data", "--yes")

        # Tenant-only data gone; SHARED data (users, tenants) untouched.
        assert Product.objects.count() == 0
        assert get_user_model().objects.count() == users_before
        assert Tenant.objects.count() >= 1
