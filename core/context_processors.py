from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_static_base_url,
)
from tenant.credentials import tenant_contact_email, tenant_site_name


def metadata(request: HttpRequest) -> dict[str, Any]:
    """Site metadata for templates rendered with a request.

    Every value that costs anything is a ``SimpleLazyObject`` (the way
    ``django.contrib.auth``'s processor hands out ``user``): a context
    processor runs for EVERY template rendered with a ``RequestContext``,
    and Unfold renders each ``{% component %}`` that way. Computed
    eagerly, the admin dashboard resolved the tenant's contact email -
    two ``extra_settings`` queries when the setting is unset, since
    extra-settings never caches a default - 59 times per page for
    values no admin template reads. Only the allauth account emails and
    the API landing page (``base.html``/``home.html``) do.
    """

    def request_details() -> dict[str, Any]:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_superuser:
            return {
                "headers": dict(request.headers),
                "cookies": request.COOKIES,
                "meta": request.META,
            }
        return {}

    return {
        "VERSION": settings.RELEASE_VERSION,
        "SITE_NAME": SimpleLazyObject(tenant_site_name),
        "SITE_DESCRIPTION": os.getenv(
            "SITE_DESCRIPTION", "Grooveshop Description"
        ),
        "SITE_KEYWORDS": os.getenv("SITE_KEYWORDS", "Grooveshop Keywords"),
        "SITE_AUTHOR": os.getenv("SITE_AUTHOR", "Grooveshop Author"),
        "SITE_URL": SimpleLazyObject(get_tenant_base_url),
        "INFO_EMAIL": SimpleLazyObject(tenant_contact_email),
        "STATIC_BASE_URL": SimpleLazyObject(get_tenant_static_base_url),
        "REQUEST_DETAILS": SimpleLazyObject(request_details),
    }
