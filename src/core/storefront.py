"""The way into the storefront's internal API, shared by every boundary
that asks it something (``texts.storefront``, ``builder.storefront``).

The base URL is ``settings.NUXT_INTERNAL_BASE_URL``, the one the cache
purge already uses. Nothing is saved on a guess: when the storefront
cannot answer, :class:`StorefrontUnavailable` is raised and the caller
refuses.
"""

from __future__ import annotations

from django.conf import settings


class StorefrontUnavailable(Exception):
    """The storefront did not give a usable answer."""


def base_url() -> str:
    base = (settings.NUXT_INTERNAL_BASE_URL or "").rstrip("/")
    if not base:
        raise StorefrontUnavailable("NUXT_INTERNAL_BASE_URL is not set")
    return base
