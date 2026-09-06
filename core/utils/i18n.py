from __future__ import annotations

from django.conf import settings


def available_language_codes() -> frozenset[str]:
    """Every language code this deployment serves content in.

    Read from ``PARLER_LANGUAGES`` rather than ``settings.LANGUAGES``
    because the callers are asking about TRANSLATION rows, and parler is
    what decides which of those exist. The two agree today (``el``,
    ``en``, ``de``); parler is the one that would still be right if a UI
    language were added without translated content behind it.

    ``SITE_ID`` first, then parler's own ``default`` bucket — the same
    order ``parler.utils.conf`` resolves in, so a per-site override does
    not silently fall back to the global list.
    """
    per_site = settings.PARLER_LANGUAGES.get(
        settings.SITE_ID
    ) or settings.PARLER_LANGUAGES.get("default", ())
    return frozenset(entry["code"] for entry in per_site)


def get_user_language(user) -> str:
    if user is None:
        return settings.LANGUAGE_CODE
    return getattr(user, "language_code", None) or settings.LANGUAGE_CODE


def get_order_language(order) -> str:
    if order is None:
        return settings.LANGUAGE_CODE
    return getattr(order, "language_code", None) or get_user_language(
        getattr(order, "user", None)
    )


def resolve_request_language(request) -> str:
    if request is None:
        return settings.LANGUAGE_CODE

    header = (
        request.headers.get("X-Language")
        or request.headers.get("X-Locale")
        or request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    )
    if not header:
        return settings.LANGUAGE_CODE

    candidate = header.split(",")[0].split("-")[0].strip().lower()
    return (
        candidate
        if candidate in available_language_codes()
        else settings.LANGUAGE_CODE
    )
