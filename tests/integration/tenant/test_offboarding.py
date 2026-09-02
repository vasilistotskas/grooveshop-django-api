"""Destroying a tenant must erase its data — and keep what the law requires.

Offboarding used to drop the Postgres schema and flush the media-stream
HTTP cache, and stop. Three things outlived the store silently: its
``{schema}__*`` Meilisearch indexes, ``MEDIA_ROOT/{schema}``, and
``_gdpr_exports/{schema}`` — whole-account PII bundles.

The naive fix is also wrong. Issued invoices carry buyer names,
addresses and VAT numbers and must NOT be deleted: GDPR art. 17(3)(b)
and art. 28(3)(g) both yield where Member State law requires storage,
and the Greek Tax Procedure Code (N. 4987/2022 art. 13) requires
accounting records kept for the statutory assessment period. Deleting
them to satisfy one regulation would breach another — and keeping them
forever would breach storage limitation (art. 5(1)(e)).

So these tests pin BOTH directions: what must go, and what must stay.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

from tenant import offboarding


def _destroyable(schema_name: str, name: str = "Acme Store"):
    """A tenant stand-in that passes every destroy gate."""
    tenant = MagicMock()
    tenant.schema_name = schema_name
    tenant.name = name
    tenant.is_active = False
    tenant.is_protected = False
    tenant.suspended_at = timezone.now() - timedelta(hours=25)
    return tenant


@pytest.fixture
def tenant_tree(tmp_path):
    """A private/public media tree shaped like the real deployment."""
    media = tmp_path / "media"
    private = tmp_path / "media_private"

    (media / "acme" / "uploads").mkdir(parents=True)
    (media / "acme" / "uploads" / "logo.png").write_text("img")
    (media / "other" / "uploads").mkdir(parents=True)
    (media / "other" / "uploads" / "keep.png").write_text("img")

    (private / "acme" / "invoices" / "2026").mkdir(parents=True)
    (private / "acme" / "invoices" / "2026" / "INV-1.pdf").write_text("pdf")
    (private / "acme" / "scratch").mkdir(parents=True)
    (private / "acme" / "scratch" / "tmp.bin").write_text("x")
    (private / "_gdpr_exports" / "acme").mkdir(parents=True)
    (private / "_gdpr_exports" / "acme" / "bundle.json").write_text("{}")
    (private / "_gdpr_exports" / "other").mkdir(parents=True)
    (private / "_gdpr_exports" / "other" / "bundle.json").write_text("{}")

    with override_settings(
        MEDIA_ROOT=str(media), PRIVATE_MEDIA_ROOT=str(private)
    ):
        yield {"media": media, "private": private}


class TestFileErasure:
    def test_public_media_is_erased(self, tenant_tree):
        offboarding.purge_tenant_files("acme")
        assert not (tenant_tree["media"] / "acme").exists()

    def test_gdpr_export_bundles_are_erased(self, tenant_tree):
        """The most sensitive files in the tree, and a sibling directory.

        They live at ``_gdpr_exports/{schema}``, NOT under
        ``{schema}/`` — deleting the per-schema tree alone would miss
        them entirely.
        """
        offboarding.purge_tenant_files("acme")
        assert not (tenant_tree["private"] / "_gdpr_exports" / "acme").exists()

    def test_invoices_are_retained(self, tenant_tree):
        """The whole point: erasure yields to the tax-record obligation."""
        offboarding.purge_tenant_files("acme")
        invoice = (
            tenant_tree["private"] / "acme" / "invoices" / "2026" / "INV-1.pdf"
        )
        assert invoice.exists(), (
            "invoices were deleted — that breaches the statutory "
            "record-keeping duty the GDPR carve-out exists for"
        )

    def test_non_invoice_private_artefacts_are_erased(self, tenant_tree):
        """Allowlist, not denylist: anything new is erased by default."""
        offboarding.purge_tenant_files("acme")
        assert not (tenant_tree["private"] / "acme" / "scratch").exists()

    def test_other_tenants_are_untouched(self, tenant_tree):
        offboarding.purge_tenant_files("acme")
        assert (
            tenant_tree["media"] / "other" / "uploads" / "keep.png"
        ).exists()
        assert (
            tenant_tree["private"] / "_gdpr_exports" / "other" / "bundle.json"
        ).exists()

    def test_missing_directories_do_not_raise(self, tenant_tree):
        """Cleanup runs after the schema is gone; it cannot abort halfway."""
        assert offboarding.purge_tenant_files("never-existed") == {
            "media": False,
            "gdpr_exports": False,
        }


class TestSearchIndexErasure:
    def _index(self, uid):
        index = MagicMock()
        index.uid = uid
        return index

    def test_only_this_tenants_indexes_are_dropped(self):
        client = MagicMock()
        client.get_indexes.return_value = [
            self._index("acme__product"),
            self._index("acme__blog"),
            self._index("other__product"),
            self._index("ProductTranslation"),
        ]
        with (
            override_settings(MEILISEARCH={"OFFLINE": False}),
            patch.dict(
                "sys.modules", {"meili._client": MagicMock(client=client)}
            ),
        ):
            dropped = offboarding.purge_search_indexes("acme")

        assert sorted(dropped) == ["acme__blog", "acme__product"]
        dropped_uids = [c.args[0] for c in client.delete_index.call_args_list]
        assert "other__product" not in dropped_uids, (
            "reached another tenant's indexes"
        )
        assert "ProductTranslation" not in dropped_uids, (
            "reached the public schema's unprefixed indexes"
        )

    def test_offline_is_a_no_op(self):
        with override_settings(MEILISEARCH={"OFFLINE": True}):
            assert offboarding.purge_search_indexes("acme") == []

    def test_client_failure_does_not_raise(self):
        client = MagicMock()
        client.get_indexes.side_effect = RuntimeError("engine down")
        with (
            override_settings(MEILISEARCH={"OFFLINE": False}),
            patch.dict(
                "sys.modules", {"meili._client": MagicMock(client=client)}
            ),
        ):
            assert offboarding.purge_search_indexes("acme") == []


class TestRetentionWindow:
    def test_anchored_on_the_tax_year_not_the_destroy_date(self):
        """The statute counts from the END of the invoice's tax year."""
        with override_settings(TENANT_INVOICE_RETENTION_YEARS=6):
            assert offboarding.retention_until(2026) == date(2032, 12, 31)

    def test_period_is_configurable_not_hardcoded(self):
        with override_settings(TENANT_INVOICE_RETENTION_YEARS=10):
            assert offboarding.retention_until(2026) == date(2036, 12, 31)

    def test_unreadable_invoices_retain_rather_than_erase(self):
        """Of the two possible mistakes, only over-retention is recoverable."""
        with patch(
            "django_tenants.utils.schema_context",
            side_effect=RuntimeError("schema gone"),
        ):
            assert offboarding.latest_invoice_year("acme") == date.today().year


@pytest.mark.django_db
class TestErasureRecord:
    """GDPR art. 5(2): compliance has to be demonstrable, not asserted."""

    def test_archive_records_basis_and_expiry(self, tenant_tree):
        from tenant.models import TenantArchive

        tenant = _destroyable("acme")

        with (
            patch.object(offboarding, "latest_invoice_year", return_value=2026),
            patch("tenant.lifecycle.has_tenant_export", return_value=True),
            override_settings(TENANT_INVOICE_RETENTION_YEARS=6),
        ):
            from tenant.lifecycle import destroy_tenant

            destroy_tenant(tenant, actor="ops@example.com")

        archive = TenantArchive.objects.get(schema_name="acme")
        assert archive.tenant_name == "Acme Store"
        assert archive.destroyed_by == "ops@example.com"
        assert archive.data_exported is True
        assert archive.retention_until == date(2032, 12, 31)
        assert "4987/2022" in archive.retention_basis
        assert archive.purged_at is None
        tenant.delete.assert_called_once_with(force_drop=True)

    def test_store_with_no_invoices_retains_nothing(self, tenant_tree):
        from tenant.models import TenantArchive

        tenant = _destroyable("acme")

        with (
            patch.object(offboarding, "latest_invoice_year", return_value=None),
            patch("tenant.lifecycle.has_tenant_export", return_value=False),
        ):
            from tenant.lifecycle import destroy_tenant

            destroy_tenant(tenant)

        archive = TenantArchive.objects.get(schema_name="acme")
        assert archive.retention_until is None
        assert archive.retained_invoice_path == ""
        assert archive.retention_basis == ""

    def test_protected_tenants_are_refused(self):
        from tenant.lifecycle import destroy_tenant

        public = _destroyable("public")
        flagged = _destroyable("acme")
        flagged.is_protected = True
        for tenant in (public, flagged):
            with pytest.raises(ValueError, match="protected"):
                destroy_tenant(tenant)
            tenant.delete.assert_not_called()

    def test_live_and_recently_suspended_tenants_are_refused(self):
        """The gates are the lifecycle function's, whoever the caller is."""
        from tenant.lifecycle import destroy_tenant

        live = _destroyable("acme")
        live.is_active = True
        with pytest.raises(ValueError, match="suspended first"):
            destroy_tenant(live)
        live.delete.assert_not_called()

        recent = _destroyable("acme")
        recent.suspended_at = timezone.now() - timedelta(hours=1)
        with pytest.raises(ValueError, match="less than"):
            destroy_tenant(recent)
        recent.delete.assert_not_called()

    def test_expired_retention_is_purged_and_stamped(self, tenant_tree):
        from django.utils import timezone

        from tenant.models import TenantArchive
        from tenant.tasks import purge_expired_tenant_archives

        invoice_dir = os.path.join(
            str(tenant_tree["private"]), "acme", "invoices"
        )
        TenantArchive.objects.create(
            schema_name="acme",
            tenant_name="Acme",
            destroyed_at=timezone.now(),
            retained_invoice_path=invoice_dir,
            retention_until=date(2020, 12, 31),
            retention_basis=offboarding.INVOICE_RETENTION_BASIS,
        )

        result = purge_expired_tenant_archives()

        assert result["purged"] == ["acme"]
        assert not os.path.isdir(invoice_dir), (
            "retention expired but invoices survived — that is a storage "
            "limitation breach (art. 5(1)(e))"
        )
        assert TenantArchive.objects.get(schema_name="acme").purged_at

    def test_unexpired_retention_is_left_alone(self, tenant_tree):
        from django.utils import timezone

        from tenant.models import TenantArchive
        from tenant.tasks import purge_expired_tenant_archives

        invoice_dir = os.path.join(
            str(tenant_tree["private"]), "acme", "invoices"
        )
        TenantArchive.objects.create(
            schema_name="acme",
            tenant_name="Acme",
            destroyed_at=timezone.now(),
            retained_invoice_path=invoice_dir,
            retention_until=date(2099, 12, 31),
            retention_basis=offboarding.INVOICE_RETENTION_BASIS,
        )

        assert purge_expired_tenant_archives()["purged"] == []
        assert os.path.isdir(invoice_dir)


class TestOneDestroyPath:
    """The admin and the platform API must not diverge again."""

    def test_api_destroy_uses_the_lifecycle_function(self):
        import inspect

        from tenant.views import TenantAdminViewSet

        source = inspect.getsource(TenantAdminViewSet.destroy)
        assert "destroy_tenant(" in source
        assert "super().destroy" not in source, (
            "DRF's perform_destroy calls instance.delete() with no "
            "force_drop, and auto_drop_schema is False — that orphans "
            "the entire Postgres schema"
        )

    def test_admin_destroy_uses_the_lifecycle_function(self):
        import inspect

        from tenant.admin import TenantAdmin

        source = inspect.getsource(TenantAdmin.destroy_tenants)
        assert "destroy_tenant(" in source
        assert "tenant.delete(force_drop=True)" not in source, (
            "admin still deletes inline; offboarding cleanup would be skipped"
        )
