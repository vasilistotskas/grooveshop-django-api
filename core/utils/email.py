from __future__ import annotations

from typing import Any

from django.conf import settings
from disposable_email_domains import blocklist as DISPOSABLE_BLOCKLIST


def is_disposable_domain(domain: str) -> bool:
    domain = domain.lower()
    if domain in DISPOSABLE_BLOCKLIST:
        return True
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in DISPOSABLE_BLOCKLIST:
            return True
    return False


def get_base_email_context() -> dict[str, Any]:
    """Shared context keys injected into every transactional/marketing email."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_URL": settings.NUXT_BASE_URL,
        "INFO_EMAIL": settings.INFO_EMAIL,
        "STATIC_BASE_URL": settings.STATIC_BASE_URL,
    }
