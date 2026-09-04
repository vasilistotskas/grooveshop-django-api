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
# django-allauth account emails (login code, password reset, email
# confirmation, security notices) live in the separate ``account/email/``
# tree — allauth's own template-lookup convention — but must be no less
# themed than the rest of transactional mail, so the sweep covers both.
ACCOUNT_EMAIL_TEMPLATE_ROOT = (
    Path(settings.BASE_DIR) / "core" / "templates" / "account"
)
SWEPT_ROOTS = (EMAIL_TEMPLATE_ROOT, ACCOUNT_EMAIL_TEMPLATE_ROOT)


class TestNoCssCustomProperties:
    def test_no_email_template_uses_var(self):
        """A single var() re-opens the invisible-CTA bug."""
        offenders = [
            str(path.relative_to(root))
            for root in SWEPT_ROOTS
            for path in root.rglob("*.html")
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
        palette. ``{% include %}`` also counts: the allauth signup
        confirmation email is a thin include of the (themed)
        add-email confirmation template, mirroring allauth's own
        ``email_confirmation_signup_message.txt`` convention.
        """
        orphans = []
        for root in SWEPT_ROOTS:
            for path in root.rglob("*.html"):
                if path.parent.name == "base":
                    continue
                text = path.read_text(encoding="utf-8")
                if "{% extends" not in text and "{% include" not in text:
                    orphans.append(str(path.relative_to(root)))
        assert orphans == [], (
            f"these bypass the shared base and its theming: {orphans}"
        )


class TestNoLeakedTemplateComments:
    """Django's ``{# … #}`` comment does not span lines.

    The comment token is matched per line, so a comment wrapped onto a
    second line is never recognised and Django emits it verbatim. One
    such comment sat in the header block of ``email_base.html``, which
    every transactional email extends, so its full text was printed
    above the logo in every message that went out. Multi-line commentary
    belongs in ``{% comment %}``/``{% endcomment %}``, which is a real
    block tag.
    """

    COMMENT_SUFFIXES = ("*.html", "*.txt")

    def test_no_template_opens_a_comment_it_does_not_close(self):
        offenders = []
        for root in SWEPT_ROOTS:
            for pattern in self.COMMENT_SUFFIXES:
                for path in root.rglob(pattern):
                    text = path.read_text(encoding="utf-8")
                    for lineno, line in enumerate(text.splitlines(), 1):
                        head, sep, tail = line.partition("{#")
                        if sep and "#}" not in tail:
                            offenders.append(
                                f"{path.relative_to(root)}:{lineno}"
                            )
        assert offenders == [], (
            "{# #} is single-line only — a comment that wraps is rendered "
            f"to the recipient; use {{% comment %}} instead: {offenders}"
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
        from tenant import credentials

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
        from tenant import credentials

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
        from tenant import credentials

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


class TestUncustomisedTenantRendersExactlyAsBefore:
    """An existing store's mail must not change appearance.

    Every ``Tenant`` row carries the model's field defaults —
    ``accent_hex`` is ``#003DFF``, ``success_hex`` is ``#16a34a`` —
    so "the field has a value" does NOT mean the merchant chose it.
    Treating a default as a customisation repainted every existing
    store's email (header ``#97b7ff`` -> ``#003DFF``, buttons
    ``#2563eb`` -> ``#003DFF``) purely because a default exists.

    These are the EXACT values from the old ``:root`` block. Rendering
    the pre-change template with its variables resolved produced output
    byte-identical to the current template under this palette.
    """

    ORIGINAL_PALETTE = {
        "primary": "#2563eb",
        "primary_dark": "#1e40af",
        "secondary": "#10b981",
        "header": "#97b7ff",
    }

    def test_platform_defaults_are_the_original_palette(self):
        from tenant.credentials import _DEFAULT_EMAIL_THEME

        assert _DEFAULT_EMAIL_THEME == self.ORIGINAL_PALETTE

    def test_tenant_carrying_model_defaults_gets_the_original_palette(
        self, monkeypatch
    ):
        from tenant import credentials
        from tenant.models import Tenant

        defaults = {
            "accent_hex": Tenant._meta.get_field("accent_hex").default,
            "success_hex": Tenant._meta.get_field("success_hex").default,
            "theme_metadata": {},
        }
        monkeypatch.setattr(
            credentials,
            "_get_tenant_field",
            lambda name, *a, **kw: defaults.get(name, ""),
        )

        assert credentials.tenant_email_theme() == self.ORIGINAL_PALETTE

    def test_customisation_is_still_honoured(self, monkeypatch):
        """The feature must still work for a store that DID choose."""
        from tenant import credentials

        values = {"accent_hex": "#AA00BB", "theme_metadata": {}}
        monkeypatch.setattr(
            credentials,
            "_get_tenant_field",
            lambda name, *a, **kw: values.get(name, ""),
        )

        theme = credentials.tenant_email_theme()
        assert theme["primary"] == "#AA00BB"
        assert theme["header"] == "#AA00BB"

    def test_default_comparison_is_case_insensitive(self, monkeypatch):
        """The hex fields are free text; #003dff is not a customisation."""
        from tenant import credentials
        from tenant.models import Tenant

        default = str(Tenant._meta.get_field("accent_hex").default)
        values = {"accent_hex": default.lower(), "theme_metadata": {}}
        monkeypatch.setattr(
            credentials,
            "_get_tenant_field",
            lambda name, *a, **kw: values.get(name, ""),
        )

        assert credentials.tenant_email_theme() == self.ORIGINAL_PALETTE
