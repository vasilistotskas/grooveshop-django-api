"""Email CSS must be literal hex, and must carry the tenant's brand.

Two defects in one place.

RENDERING: ``email_base.html`` styled everything through CSS custom
properties — 22 ``var(--x)`` references, none with a fallback. Per
caniemail, Gmail, Outlook, Apple Mail, Yahoo and Thunderbird accept the
``var()`` FUNCTION but drop the ``:root { --x: … }`` DECLARATION, which
makes every reference invalid at computed-value time. The primary CTA
paired ``background-color: var(--primary-color)`` with
``color: #ffffff !important``, so it degraded to white text on a white
card — an invisible button, not merely an unstyled one, in every
transactional email.

BRANDING: the header colour was hardcoded ``#97b7ff`` and no tenant
theme field reached email at all, so every tenant's mail wore the same
palette.

Both are fixed by resolving brand colours to literal hex server-side
(``tenant_email_theme``) and rendering them into the template, which
Django does before the mail ever leaves — zero client dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string

from core.utils.email_context import build_email_context

EMAIL_TEMPLATE_ROOT = Path(settings.BASE_DIR) / "core" / "templates" / "emails"


class TestNoCssCustomProperties:
    def test_no_email_template_uses_var(self):
        """A single var() re-opens the invisible-CTA bug."""
        offenders = [
            str(path.relative_to(EMAIL_TEMPLATE_ROOT))
            for path in EMAIL_TEMPLATE_ROOT.rglob("*.html")
            if "var(--" in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], (
            "CSS custom properties do not survive major email clients — "
            f"use literal hex or a THEME value instead: {offenders}"
        )

    def test_base_declares_no_root_block(self):
        base = (EMAIL_TEMPLATE_ROOT / "base" / "email_base.html").read_text(
            encoding="utf-8"
        )
        assert ":root {" not in base

    def test_every_email_template_extends_the_base(self):
        """The base is where branding and theming live.

        BoxNow was the lone exception and carried its own hardcoded
        palette.
        """
        orphans = []
        for path in EMAIL_TEMPLATE_ROOT.rglob("*.html"):
            if path.parent.name == "base":
                continue
            if "{% extends" not in path.read_text(encoding="utf-8"):
                orphans.append(str(path.relative_to(EMAIL_TEMPLATE_ROOT)))
        assert orphans == [], (
            f"these bypass the shared base and its theming: {orphans}"
        )


@pytest.mark.django_db
class TestRenderedOutput:
    def test_rendered_email_contains_no_unresolved_css(self):
        html = render_to_string(
            "emails/order/order_shipped.html",
            build_email_context(order={"id": 1}, items=[]),
        )
        assert "var(--" not in html
        assert "{{" not in html

    def test_button_gets_a_real_background_colour(self):
        """The exact declaration that used to resolve to transparent."""
        html = render_to_string(
            "emails/order/order_shipped.html",
            build_email_context(order={"id": 1}, items=[]),
        )
        theme = build_email_context()["THEME"]
        assert f"background-color: {theme['primary']}" in html
        assert theme["primary"].startswith("#")

    def test_boxnow_inherits_the_shared_base(self):
        html = render_to_string(
            "emails/order/boxnow_parcel_at_locker.html",
            build_email_context(
                order={"id": 7, "first_name": "Maria"},
                shipment={"parcel_id": "BN123"},
                locker_address="Somewhere 1",
                locker=None,
            ),
        )
        assert "#1c2d5e" not in html, "old hardcoded BoxNow palette survived"
        assert "email-wrapper" in html, "did not render through the base"


class TestThemeResolution:
    def test_defaults_are_literal_hex(self):
        from tenant.credentials import tenant_email_theme

        theme = tenant_email_theme()
        assert set(theme) == {"primary", "primary_dark", "secondary", "header"}
        for key, value in theme.items():
            assert value.startswith("#"), f"{key} is not hex: {value!r}"

    def test_accent_hex_drives_the_palette(self, monkeypatch):
        import tenant.credentials as credentials

        values = {"accent_hex": "#AA00BB", "success_hex": "#00CC44"}
        monkeypatch.setattr(
            credentials,
            "_get_tenant_field",
            lambda name, *a, **kw: values.get(name, ""),
        )

        theme = credentials.tenant_email_theme()

        assert theme["primary"] == "#AA00BB"
        assert theme["header"] == "#AA00BB"
        assert theme["secondary"] == "#00CC44"

    def test_theme_metadata_scale_wins_over_accent(self, monkeypatch):
        import tenant.credentials as credentials

        values = {
            "accent_hex": "#AA00BB",
            "theme_metadata": {
                "colors": {"primaryScale": {"600": "#123456", "700": "#0A1B2C"}}
            },
        }
        monkeypatch.setattr(
            credentials,
            "_get_tenant_field",
            lambda name, *a, **kw: values.get(name, ""),
        )

        theme = credentials.tenant_email_theme()

        assert theme["primary"] == "#123456"
        assert theme["primary_dark"] == "#0A1B2C"

    def test_malformed_theme_metadata_falls_back_cleanly(self, monkeypatch):
        """Merchant-editable JSON must never break outbound mail."""
        import tenant.credentials as credentials

        for junk in ("not-a-dict", {"colors": "nope"}, {"colors": {}}, None):
            monkeypatch.setattr(
                credentials,
                "_get_tenant_field",
                lambda name, *a, _j=junk, **kw: (
                    _j if name == "theme_metadata" else ""
                ),
            )
            theme = credentials.tenant_email_theme()
            assert theme["primary"].startswith("#")
