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
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string

from builder.factories import (
    BuilderDocumentFactory,
    BuilderReleaseFactory,
    BuilderVersionFactory,
)
from builder.models import DocumentKind
from builder.published import published_index
from core.utils.email_context import build_email_context
from tenant.credentials import (
    _DEFAULT_EMAIL_THEME,
    in_tenant_schema,
    tenant_email_theme,
)
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

EMAIL_TEMPLATE_ROOT = Path(settings.SRC_DIR) / "core" / "templates" / "emails"
# django-allauth emails (login code, password reset, email confirmation,
# security notices, MFA and social-account notices) live in allauth's own
# template-lookup trees (``account/``, ``mfa/``, ``socialaccount/``) but
# must be no less themed than the rest of transactional mail, so the sweep
# covers all of them.
TEMPLATES = Path(settings.SRC_DIR) / "core" / "templates"
SWEPT_ROOTS = (
    EMAIL_TEMPLATE_ROOT,
    TEMPLATES / "account",
    TEMPLATES / "mfa",
    TEMPLATES / "socialaccount",
)


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
                        _head, sep, tail = line.partition("{#")
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
            build_email_context(language="el", order={"id": 1}, items=[]),
        )
        assert "var(--" not in html
        assert "{{" not in html

    def test_button_gets_a_real_background_colour(self):
        """The exact declaration that used to resolve to transparent."""
        html = render_to_string(
            "emails/order/order_shipped.html",
            build_email_context(language="el", order={"id": 1}, items=[]),
        )
        theme = build_email_context(language="el")["THEME"]
        assert f"background-color: {theme['primary']}" in html
        assert theme["primary"].startswith("#")

    def test_boxnow_inherits_the_shared_base(self):
        html = render_to_string(
            "emails/order/boxnow_parcel_at_locker.html",
            build_email_context(
                language="el",
                order={"id": 7, "first_name": "Maria"},
                shipment={"parcel_id": "BN123"},
                locker_address="Somewhere 1",
                locker=None,
            ),
        )
        assert "#1c2d5e" not in html, "old hardcoded BoxNow palette survived"
        assert "email-wrapper" in html, "did not render through the base"


ORIGINAL_PALETTE = {
    "primary": "#2563eb",
    "primary_dark": "#1e40af",
    "secondary": "#10b981",
    "header": "#97b7ff",
}

#: The primary scale of ``ekfyseosfyteias``, the one production store that
#: chose its own (its published theme document, shades 600 and 700).
FYTEIA_600, FYTEIA_700 = "#7f8a6c", "#676d58"


def _default_primary_scale() -> dict[str, str]:
    """The scale every new store's theme starts with."""
    from builder import seeding

    defaults = seeding.seed_catalogue().roots["theme"].defaults
    return dict(defaults["colors"]["light"]["primary"]["scale"])


def _release_theme(
    scale: dict[str, str] | None, *, dark: dict | None = None, **colors
) -> None:
    """Release a theme document whose light primary is *scale*."""
    primary = {"scale": scale} if scale is not None else None
    light = {**({"primary": primary} if primary else {}), **colors}
    document = BuilderDocumentFactory(kind=DocumentKind.THEME, key="theme")
    version = BuilderVersionFactory(
        document=document,
        data={
            "sections": {},
            "order": [],
            "settings": {
                "colors": {"light": light, **({"dark": dark} if dark else {})}
            },
        },
    )
    BuilderReleaseFactory(versions={"theme": version.pk})


@pytest.fixture
def store():
    """A store schema is active, with a cold cache."""
    cache.clear()
    tenant = store_tenant("email_theme_store")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)
    cache.clear()


@pytest.mark.django_db
class TestThemeResolution:
    """``tenant_email_theme`` wears the store's published theme document."""

    def test_defaults_are_literal_hex(self):
        theme = tenant_email_theme()
        assert set(theme) == {"primary", "primary_dark", "secondary", "header"}
        for key, value in theme.items():
            assert value.startswith("#"), f"{key} is not hex: {value!r}"

    def test_platform_defaults_are_the_original_palette(self):
        """The values of the old ``:root`` block, byte for byte."""
        assert _DEFAULT_EMAIL_THEME == ORIGINAL_PALETTE

    def test_primary_scale_drives_the_palette(self, store):
        _release_theme(
            {**_default_primary_scale(), "600": "#123456", "700": "#0a1b2c"}
        )

        assert tenant_email_theme() == {
            "primary": "#123456",
            "primary_dark": "#0a1b2c",
            "secondary": ORIGINAL_PALETTE["secondary"],
            "header": "#123456",
        }

    def test_the_store_that_chose_its_scale_keeps_its_colours(self, store):
        """ekfyseosfyteias: shades 600 and 700 of its sage scale."""
        _release_theme(
            {**_default_primary_scale(), "600": FYTEIA_600, "700": FYTEIA_700}
        )

        assert tenant_email_theme() == {
            "primary": FYTEIA_600,
            "primary_dark": FYTEIA_700,
            "secondary": ORIGINAL_PALETTE["secondary"],
            "header": FYTEIA_600,
        }

    def test_accent_and_status_colours_are_not_read(self, store):
        """What an unchanged store paints depends on its design, which the
        API does not know, so only the primary scale is a brand."""
        _release_theme(
            _default_primary_scale(), accent="#b3694b", success="#13703a"
        )

        assert tenant_email_theme() == ORIGINAL_PALETTE

    def test_dark_scheme_is_not_read(self, store):
        _release_theme(
            _default_primary_scale(),
            dark={"primary": {"scale": {"600": "#123456", "700": "#0a1b2c"}}},
        )

        assert tenant_email_theme() == ORIGINAL_PALETTE


@pytest.mark.django_db
class TestUnbrandedStoreRendersExactlyAsBefore:
    """An existing store's mail must not change appearance.

    Every store's theme document carries a primary scale: a new store
    starts with the platform's, and the theme import wrote the two
    production stores that never chose one (demo, webside) the same.
    "The document has a scale" is therefore NOT "the merchant chose it".
    Treating it so repainted their mail grey (``#525252``) purely because
    a default exists, instead of the palette it has always worn.
    """

    def test_default_scale_gets_the_original_palette(self, store):
        """demo and webside."""
        _release_theme(_default_primary_scale())

        assert tenant_email_theme() == ORIGINAL_PALETTE

    def test_default_comparison_is_case_insensitive(self, store):
        """The real default's shades have no letters, so the comparison is
        held against one that does."""
        default = {"600": "#ab12ef", "700": "#cd34ef"}
        catalogue = SimpleNamespace(
            roots={
                "theme": SimpleNamespace(
                    defaults={
                        "colors": {"light": {"primary": {"scale": default}}}
                    }
                )
            }
        )
        _release_theme(
            {
                **_default_primary_scale(),
                "600": default["600"].upper(),
                "700": default["700"].upper(),
            }
        )

        with mock.patch(
            "builder.seeding.seed_catalogue", return_value=catalogue
        ):
            assert tenant_email_theme() == ORIGINAL_PALETTE

    def test_a_scale_that_changes_only_its_dark_shade_is_the_stores_own(
        self, store
    ):
        scale = _default_primary_scale()
        _release_theme({**scale, "700": "#0a1b2c"})

        theme = tenant_email_theme()

        assert theme["primary"] == scale["600"]
        assert theme["primary_dark"] == "#0a1b2c"

    def test_store_with_no_release_gets_the_original_palette(
        self, store, caplog
    ):
        """A normal state, not a warning."""
        assert tenant_email_theme() == ORIGINAL_PALETTE
        assert caplog.text == ""

    def test_release_without_a_theme_gets_the_original_palette(
        self, store, caplog
    ):
        BuilderReleaseFactory(versions={})

        assert tenant_email_theme() == ORIGINAL_PALETTE
        assert caplog.text == ""

    def test_platform_mail_under_public_gets_the_original_palette(self):
        """Platform mail renders in ``public``: no store, so no theme, even
        where a release is on disk (a store-less Celery task, a shell)."""
        cache.clear()
        _release_theme({**_default_primary_scale(), "600": "#123456"})
        assert in_tenant_schema() is False

        assert tenant_email_theme() == ORIGINAL_PALETTE


@pytest.mark.django_db
class TestAnUnreadableThemeNeverStopsAMail:
    """Merchant-edited JSON and the infrastructure under it must never
    break outbound mail."""

    @pytest.mark.parametrize(
        "scale",
        [
            {},
            {"600": "#123456"},
            {"700": "#123456"},
            {"600": "blue", "700": "blue"},
            {"600": "#12345", "700": "#0a1b2c"},
            {"600": "#fff;}</style><script>", "700": "#0a1b2c"},
            {"600": 7, "700": None},
        ],
    )
    def test_malformed_scale_gets_the_original_palette(
        self, store, scale, caplog
    ):
        _release_theme(scale)

        assert tenant_email_theme() == ORIGINAL_PALETTE
        assert "email colours" in caplog.text

    def test_document_without_colours_gets_the_original_palette(self, store):
        _release_theme(None)

        assert tenant_email_theme() == ORIGINAL_PALETTE

    def test_a_failed_read_is_logged_and_gets_the_original_palette(
        self, store, caplog
    ):
        with mock.patch(
            "builder.published.published_index", side_effect=RuntimeError("db")
        ):
            theme = tenant_email_theme()

        assert theme == ORIGINAL_PALETTE
        assert "Could not read the published theme" in caplog.text


@pytest.mark.django_db
class TestTheReadIsCheap:
    def test_a_warm_release_costs_no_query(
        self, store, django_assert_num_queries
    ):
        _release_theme({**_default_primary_scale(), "600": "#123456"})
        assert tenant_email_theme()["primary"] == "#123456"

        with django_assert_num_queries(0):
            assert tenant_email_theme()["primary"] == "#123456"

    def test_the_read_is_the_storefronts_cached_chrome(self, store):
        """No cache of its own: the storefront's request for the default
        locale warms the mail's read."""
        _release_theme({**_default_primary_scale(), "600": "#123456"})
        published_index(settings.PARLER_DEFAULT_LANGUAGE_CODE)

        with mock.patch("builder.published._build_index") as build:
            assert tenant_email_theme()["primary"] == "#123456"
        build.assert_not_called()


@pytest.mark.django_db
class TestRenderedMailWearsTheStoresColours:
    def test_button_and_header_follow_the_document(self, store):
        _release_theme(
            {**_default_primary_scale(), "600": FYTEIA_600, "700": FYTEIA_700}
        )

        html = render_to_string(
            "emails/order/order_shipped.html",
            build_email_context(language="el", order={"id": 1}, items=[]),
        )

        assert f"background-color: {FYTEIA_600}" in html
        assert f"background-color: {FYTEIA_700}" in html
        assert ORIGINAL_PALETTE["primary"] not in html
        assert ORIGINAL_PALETTE["header"] not in html
