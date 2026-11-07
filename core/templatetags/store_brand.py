"""The store's brand for the pages Django renders itself.

allauth's sign-in, sign-up, password, code and MFA pages, and the OAuth
consent an AI agent sends a shopper to, are served on the store's API
host. They wear the same marks as the store's email and admin: its name,
logo, favicon and primary colour, from the helpers those already use.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.translation import get_language

from admin.branding import site_favicons, site_logo
from core.utils.tenant_urls import get_tenant_frontend_url
from tenant.credentials import tenant_email_theme, tenant_site_name

register = template.Library()


@register.simple_tag(takes_context=True)
def store_brand(context: template.Context) -> dict[str, Any]:
    request = context.get("request")
    theme = tenant_email_theme()
    favicons = site_favicons(request)
    return {
        "name": tenant_site_name(),
        # {"light", "dark"}, or None: an unbranded store shows its name.
        "logo": site_logo(request),
        "favicon": favicons[0]["href"] if favicons else "",
        # Literal hex, validated where it is read, so it may go into a
        # style attribute.
        "primary": theme["primary"],
        "primary_dark": theme["primary_dark"],
        "home_url": get_tenant_frontend_url("", language=get_language()),
    }
