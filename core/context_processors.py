from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from typing import Any

from django.conf import settings
from django.http import HttpRequest


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
    """
    Context processor that adds site metadata to template context.

    Provides site information and request details (for superusers only).

    Args:
        request: HTTP request object

    Returns:
        Dictionary with site metadata and optional request details
    """
    site_name = os.getenv("SITE_NAME", "Grooveshop")
    site_description = os.getenv("SITE_DESCRIPTION", "Grooveshop Description")
    site_keywords = os.getenv("SITE_KEYWORDS", "Grooveshop Keywords")
    site_author = os.getenv("SITE_AUTHOR", "Grooveshop Author")

    request_details: dict[str, Any] = {}
    if request.user and request.user.is_superuser:
        request_details = {
            "headers": dict(request.headers),
            "cookies": request.COOKIES,
            "meta": request.META,
        }

    return {
        "VERSION": get_version_from_toml(),
        "SITE_NAME": site_name,
        "SITE_DESCRIPTION": site_description,
        "SITE_KEYWORDS": site_keywords,
        "SITE_AUTHOR": site_author,
        "SITE_URL": settings.NUXT_BASE_URL,
        "INFO_EMAIL": settings.INFO_EMAIL,
        "STATIC_BASE_URL": settings.STATIC_BASE_URL,
        "REQUEST_DETAILS": request_details,
    }
