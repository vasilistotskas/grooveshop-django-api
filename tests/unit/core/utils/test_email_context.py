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


def _fake_tenant(**overrides):
    defaults = {
        "schema_name": "email-ctx-tenant",
        "store_name": "",
        "name": "",
        "contact_email": "",
        "logo_light_url": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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

    def test_site_logo_url_empty_when_tenant_has_no_logo(self, bind_tenant):
        bind_tenant(_fake_tenant(logo_light_url=""))

        context = build_email_context()

        # The template's fallback branch (STATIC_BASE_URL + the
        # platform's logo-dark.svg) is what renders when this is
        # empty — the byte-parity guard for tenants without a logo.
        assert context["SITE_LOGO_URL"] == ""

    def test_site_logo_url_empty_with_no_active_tenant(self, bind_tenant):
        bind_tenant(None)

        context = build_email_context()

        assert context["SITE_LOGO_URL"] == ""
