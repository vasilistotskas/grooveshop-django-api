"""Tests for ``core.utils.email_context.build_email_context``.

Every Celery email task routes its render context through this single
function so the ``SITE_LOGO_URL`` branch in
``core/templates/emails/base/email_base.html`` actually receives a
value — previously no live send included that key at all, so a
tenant's configured ``logo_light_url`` never reached its outbound
mail (see the module docstring on ``core/utils/email_context.py`` for
the full defect writeup).
"""

from __future__ import annotations

from types import SimpleNamespace

from core.utils.email_context import build_email_context


def _domains(domain: str):
    """Minimal stand-in for the TenantDomain related manager."""
    primary = SimpleNamespace(domain=domain, is_primary=True)
    query = SimpleNamespace(first=lambda: primary)
    return SimpleNamespace(filter=lambda **kw: query)


def _fake_tenant(domain: str | None = None, **overrides):
    defaults = {
        "schema_name": "email-ctx-tenant",
        "store_name": "",
        "name": "",
        "contact_email": "",
        "logo_light_url": "",
    }
    defaults.update(overrides)
    tenant = SimpleNamespace(**defaults)
    if domain is not None:
        tenant.domains = _domains(domain)
    return tenant


class TestBuildEmailContext:
    def test_includes_every_shared_key(self, bind_tenant):
        bind_tenant(_fake_tenant(store_name="Acme"))

        context = build_email_context()

        assert set(context) == {
            "SITE_NAME",
            "SITE_URL",
            "INFO_EMAIL",
            "STATIC_BASE_URL",
            "SITE_LOGO_URL",
        }
        assert context["SITE_NAME"] == "Acme"

    def test_extra_kwargs_are_merged_in(self, bind_tenant):
        bind_tenant(_fake_tenant())

        context = build_email_context(order="ORDER-1", items=[1, 2])

        assert context["order"] == "ORDER-1"
        assert context["items"] == [1, 2]

    def test_extra_kwarg_overrides_a_shared_key(self, bind_tenant):
        bind_tenant(_fake_tenant(contact_email="tenant@example.com"))

        context = build_email_context(INFO_EMAIL="staff@example.com")

        # e.g. admin-notification tasks that must reply to a staff
        # address rather than the tenant's public contact address.
        assert context["INFO_EMAIL"] == "staff@example.com"

    def test_site_logo_url_present_when_tenant_has_logo(self, bind_tenant):
        bind_tenant(
            _fake_tenant(logo_light_url="https://cdn.example.com/logo.svg")
        )

        context = build_email_context()

        assert context["SITE_LOGO_URL"] == "https://cdn.example.com/logo.svg"

    def test_unbranded_non_platform_tenant_gets_no_logo(self, bind_tenant):
        # An unbranded tenant's emails must never wear the platform's
        # brand — empty makes email_base.html render the store name as
        # a text wordmark instead.
        bind_tenant(_fake_tenant(domain="shop.acme.example", logo_light_url=""))

        context = build_email_context()

        assert context["SITE_LOGO_URL"] == ""

    def test_platform_tenant_falls_back_to_platform_logo(
        self, bind_tenant, settings
    ):
        settings.NUXT_BASE_URL = "https://platform.example"
        bind_tenant(_fake_tenant(domain="platform.example", logo_light_url=""))

        context = build_email_context()

        assert context["SITE_LOGO_URL"].endswith("/static/logo-dark.svg")

    def test_no_active_tenant_counts_as_platform(self, bind_tenant):
        # Public-schema/admin contexts keep the platform logo.
        bind_tenant(None)

        context = build_email_context()

        assert context["SITE_LOGO_URL"].endswith("/static/logo-dark.svg")
