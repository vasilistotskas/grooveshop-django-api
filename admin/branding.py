"""Per-store branding for the store admin's Unfold chrome.

Unfold resolves ``SITE_LOGO``, ``SITE_ICON`` and ``SITE_FAVICONS``
through dotted paths to callables taking the request, so each store's
admin wears that store's own marks. The rule is the one outbound email
follows (``core.utils.email_context._email_logo_url``):

- the tenant's own asset when it has one;
- the platform's bundled assets (``static/logo-*.svg``, ``icon-*.svg``,
  ``favicon/favicon.svg``) ONLY for the platform's own storefront
  (``Tenant.is_platform_storefront``);
- nothing otherwise. Unfold then draws ``SITE_SYMBOL`` beside the store
  name, so an unbranded store never wears another store's brand.
"""

from __future__ import annotations

from typing import Any

from django.templatetags.static import static

from tenant.membership import get_current_tenant


def _theme_pair(light: str, dark: str) -> dict[str, str] | None:
    """Unfold's ``{"light", "dark"}`` shape; one set value serves both."""
    if not (light or dark):
        return None
    return {"light": light or dark, "dark": dark or light}


def site_logo(request: Any) -> dict[str, str] | None:
    tenant = get_current_tenant()
    if tenant is None:
        return None
    own = _theme_pair(tenant.logo_light_url, tenant.logo_dark_url)
    if own is not None:
        return own
    if tenant.is_platform_storefront:
        return {
            "light": static("logo-light.svg"),
            "dark": static("logo-dark.svg"),
        }
    return None


def site_icon(request: Any) -> dict[str, str] | None:
    tenant = get_current_tenant()
    if tenant is not None and tenant.is_platform_storefront:
        return {
            "light": static("icon-light.svg"),
            "dark": static("icon-dark.svg"),
        }
    return None


def site_favicons(request: Any) -> list[dict[str, str]]:
    tenant = get_current_tenant()
    if tenant is None:
        return []
    if tenant.favicon_url:
        return [{"rel": "icon", "href": tenant.favicon_url}]
    if tenant.is_platform_storefront:
        return [
            {
                "rel": "icon",
                "sizes": "32x32",
                "type": "image/svg+xml",
                "href": static("favicon/favicon.svg"),
            }
        ]
    return []
