"""Provisioning steps a fresh tenant needs but was not getting.

Three separate gaps, each of which left a newly provisioned store
broken or degraded in a way nothing surfaced:

* Meilisearch indexes were created WITHOUT their settings, so
  ``filterableAttributes`` stayed at the engine default ``[]`` while
  every storefront search sends a filter — the engine rejects it and
  ``search/views.py`` turns that into HTTP 400. Settings only arrived
  with the nightly fanout sync or the next deploy's PreSync hook, a
  window of up to ~24h with no working search at all.
* No ``django.contrib.sites`` Site row was created, so per-tenant
  ``SocialApp`` credentials — which allauth resolves BY Site — were
  unreachable and silently fell back to the platform's OAuth app.
* Stripe provisioning existed only as a CLI command with no
  programmatic caller, so a merchant who saved their Stripe key got no
  webhook endpoint and their Stripe orders never confirmed.

The suite runs with multi-tenancy disabled (``tests/conftest.py``), so
these assert the wiring rather than replaying a real schema creation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestMeiliIndexesGetTheirSettings:
    def test_provisioning_applies_settings_not_just_create_index(self):
        """``create_index`` alone leaves filterableAttributes empty.

        ``update_meili_settings`` still guarantees the primary key — it
        calls ``create_index(index_name, primary_key)`` itself first —
        so this strictly adds the settings step.
        """
        from tenant import provisioning

        model = MagicMock()
        model.get_meili_index_name.return_value = "tenant__product"

        with (
            patch.object(provisioning, "logger"),
            patch.dict(
                "sys.modules",
            ),
        ):
            from django.test import override_settings

            with override_settings(MEILISEARCH={"OFFLINE": False}):
                with patch(
                    "meili.models.IndexMixin.__subclasses__",
                    return_value=[model],
                ):
                    provisioning._create_meili_indexes(MagicMock())

        model.update_meili_settings.assert_called_once_with()

    def test_offline_short_circuits(self):
        from django.test import override_settings

        from tenant import provisioning

        model = MagicMock()
        with (
            override_settings(MEILISEARCH={"OFFLINE": True}),
            patch(
                "meili.models.IndexMixin.__subclasses__", return_value=[model]
            ),
        ):
            provisioning._create_meili_indexes(MagicMock())

        model.update_meili_settings.assert_not_called()


@pytest.mark.django_db
class TestEnsureSite:
    """Per-tenant SocialApp credentials are resolved BY Site row."""

    def _tenant(self, domain: str | None, name="Acme", store_name="Acme Store"):
        tenant = MagicMock()
        tenant.name = name
        tenant.store_name = store_name
        primary = MagicMock()
        primary.domain = domain
        tenant.domains.filter.return_value.first.return_value = (
            primary if domain else None
        )
        return tenant

    def test_creates_a_site_for_the_primary_domain(self):
        from django.contrib.sites.models import Site

        from tenant.provisioning import ensure_site

        Site.objects.filter(domain="acme.example").delete()

        result = ensure_site(self._tenant("acme.example"))

        assert result == "acme.example"
        site = Site.objects.get(domain="acme.example")
        assert site.name == "Acme Store"

    def test_is_idempotent_and_never_rewrites_an_existing_name(self):
        """An operator may have renamed the Site deliberately."""
        from django.contrib.sites.models import Site

        from tenant.provisioning import ensure_site

        Site.objects.filter(domain="acme.example").delete()
        Site.objects.create(domain="acme.example", name="Operator Chosen")

        ensure_site(self._tenant("acme.example"))
        ensure_site(self._tenant("acme.example"))

        assert Site.objects.filter(domain="acme.example").count() == 1
        assert Site.objects.get(domain="acme.example").name == "Operator Chosen"

    def test_returns_none_without_a_primary_domain(self):
        """Matches ensure_api_domain's contract — cannot act, does not raise."""
        from tenant.provisioning import ensure_site

        assert ensure_site(self._tenant(None)) is None

    def test_falls_back_to_tenant_name_when_store_name_is_blank(self):
        from django.contrib.sites.models import Site

        from tenant.provisioning import ensure_site

        Site.objects.filter(domain="blank.example").delete()
        ensure_site(self._tenant("blank.example", store_name=""))

        assert Site.objects.get(domain="blank.example").name == "Acme"


class TestProvisionStripeIsReusable:
    """The command and the admin action must share one implementation."""

    def test_command_delegates_to_provisioning(self):
        import inspect

        from tenant.management.commands import bootstrap_stripe

        source = inspect.getsource(bootstrap_stripe)
        assert "provision_stripe" in source
        # The superseded in-command implementation is gone, not kept
        # alongside it.
        assert "_api_create" not in source, (
            "bootstrap_stripe still carries its own Stripe implementation; "
            "it must delegate to tenant.provisioning.provision_stripe"
        )

    def test_admin_exposes_the_action(self):
        from tenant.admin import TenantAdmin

        assert "provision_stripe_webhook" in TenantAdmin.actions

    def test_action_is_not_stripped_from_merchants(self):
        """Saving a Stripe key IS the moment this needs to run.

        get_actions removes the platform lifecycle levers for
        self-service tenants; Stripe provisioning must not be in that
        list, or the gap this closes stays open for merchants.
        """
        import inspect

        from tenant.admin import TenantAdmin

        source = inspect.getsource(TenantAdmin.get_actions)
        assert "provision_stripe_webhook" not in source

    def test_reports_missing_key_without_calling_stripe(self):
        from tenant.provisioning import provision_stripe

        tenant = MagicMock()
        with (
            patch("django_tenants.utils.tenant_context"),
            patch(
                "tenant.credentials.stripe_credentials",
                return_value={"secret_key": ""},
            ),
        ):
            result = provision_stripe(tenant)

        assert result["status"] == "no_key"
