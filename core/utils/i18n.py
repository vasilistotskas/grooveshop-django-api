from __future__ import annotations

from django.conf import settings


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
    valid = {code for code, _name in settings.LANGUAGES}
    return candidate if candidate in valid else settings.LANGUAGE_CODE
