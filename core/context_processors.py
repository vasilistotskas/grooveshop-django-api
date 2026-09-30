from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from typing import Any

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_static_base_url,
)
from tenant.credentials import tenant_contact_email, tenant_site_name


@lru_cache(maxsize=1)
def get_version_from_toml() -> str:
    """
    Read version from pyproject.toml file.

    Uses Python 3.11+ tomllib for proper TOML parsing.
    Cached to avoid repeated file reads.

    Returns:
        Version string from pyproject.toml, or "0.0.0" if not found
    """
    try:
        with open("pyproject.toml", "rb") as toml_file:
            data = tomllib.load(toml_file)
            version = data.get("project", {}).get("version", "0.0.0")
            return version
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError) as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to read version from pyproject.toml: {e}")
        return "0.0.0"


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
        "VERSION": get_version_from_toml(),
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
