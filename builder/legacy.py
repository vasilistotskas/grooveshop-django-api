"""A store's page_config layouts and menus, as the storefront's converter
reads them.

``page_config`` (``PageLayout`` + ``PageSection``, ``NavigationMenu``) is
what the page builder replaces. The conversion into documents is ONE
implementation, the storefront's ``fromLegacy`` (its blocks know their own
settings), reached through ``builder.convert``; this module only describes
the legacy data in the request shape the two sides agreed
(``docs/builder.md``, "Importing the legacy layouts"). Nothing here
changes a row.

A layout becomes ``template:<page_type>`` when its page type is one of the
catalogue's fixed ``templates`` and ``page:<page_type>`` otherwise, a menu
slot the group ``group:<slot>``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from builder.models import DocumentKind
from core.utils.i18n import available_language_codes
from page_config.models import (
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    PageLayout,
)
from tenant.membership import bound_tenant

SEO_FIELDS = {
    "seo_title": "seoTitle",
    "seo_description": "seoDescription",
    "seo_keywords": "seoKeywords",
}


def default_locale() -> str:
    return settings.PARLER_DEFAULT_LANGUAGE_CODE


def store_locales() -> list[str]:
    """The languages this store serves, the default first. An empty
    ``Tenant.available_locales`` means the default language only."""
    tenant = bound_tenant()
    chosen = list(getattr(tenant, "available_locales", None) or [])
    known = available_language_codes()
    locales = [code for code in chosen if code in known]
    return [default_locale(), *(c for c in locales if c != default_locale())]


def layout_kind(layout: PageLayout, templates: frozenset[str]) -> DocumentKind:
    """A layout of a fixed page type (the catalogue's ``templates``) is that
    template; any other is a page the store made."""
    if layout.page_type in templates:
        return DocumentKind.TEMPLATE
    return DocumentKind.PAGE


def menu_key(menu: NavigationMenu) -> str:
    return f"{DocumentKind.GROUP.value}:{menu.slot}"


def export_layout(layout: PageLayout) -> dict[str, Any]:
    seo = {
        translation.language_code: {
            camel: getattr(translation, field)
            for field, camel in SEO_FIELDS.items()
            if getattr(translation, field)
        }
        for translation in layout.translations.all()
    }
    return {
        "pageType": layout.page_type,
        "title": layout.title,
        "seo": {code: fields for code, fields in seo.items() if fields},
        "sections": [
            {
                "id": str(section.uuid),
                "type": section.component_type,
                "title": section.title,
                "isVisible": section.is_visible,
                "props": section.props,
                "i18n": section.i18n,
            }
            for section in layout.sections.order_by("sort_order", "pk")
        ],
    }


def _target(link: NavigationLink) -> dict[str, str]:
    if link.content_page_id is not None:
        return {"type": "content_page", "slug": link.content_page.slug}
    if link.page_layout_id is not None:
        return {"type": "page_layout", "pageType": link.page_layout.page_type}
    if link.route:
        return {"type": "route", "route": link.route}
    return {"type": "url", "href": link.url}


def _link(link: NavigationLink, locales: list[str]) -> dict[str, Any]:
    """A link with the label it would show in each language: the override
    if it has one, else the page's own title."""
    return {
        "id": str(link.uuid),
        "labels": {code: link.resolved_label(code) for code in locales},
        "target": _target(link),
    }


def _column(column: NavigationColumn, locales: list[str]) -> dict[str, Any]:
    held = {t.language_code: t.label for t in column.translations.all()}
    return {
        "id": str(column.uuid),
        "labels": {code: held[code] for code in locales if held.get(code)},
        "icon": column.icon,
        "showsMerchantIdentity": column.shows_merchant_identity,
        "links": [
            _link(link, locales)
            for link in column.links.order_by("sort_order", "pk")
        ],
    }


def export_menu(menu: NavigationMenu, locales: list[str]) -> dict[str, Any]:
    legacy: dict[str, Any] = {"slot": menu.slot}
    if menu.columns.exists():
        legacy["columns"] = [
            _column(column, locales)
            for column in menu.columns.order_by("sort_order", "pk")
        ]
    else:
        legacy["links"] = [
            _link(link, locales)
            for link in menu.links.order_by("sort_order", "pk")
        ]
    return legacy


def convert_requests(
    locales: list[str], templates: frozenset[str]
) -> list[tuple[str, bool, dict[str, Any]]]:
    """``(document key, published, convert request body)`` for everything
    the store has: each layout (published only if its flag says so) and
    each menu slot with a row (a menu has no flag: it is live)."""
    requests = []
    for layout in PageLayout.objects.prefetch_related("translations"):
        kind = layout_kind(layout, templates)
        key = f"{kind.value}:{layout.page_type}"
        requests.append(
            (
                key,
                layout.is_published,
                {
                    "kind": kind.value,
                    "key": key,
                    "legacy": export_layout(layout),
                },
            )
        )
    for menu in NavigationMenu.objects.order_by("slot"):
        key = menu_key(menu)
        requests.append(
            (
                key,
                True,
                {
                    "kind": DocumentKind.GROUP.value,
                    "key": key,
                    "legacy": export_menu(menu, locales),
                },
            )
        )
    return requests
