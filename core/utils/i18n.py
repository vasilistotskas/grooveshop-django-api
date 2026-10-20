from __future__ import annotations

from django.conf import settings
from django.utils.translation import get_language


def available_language_codes() -> frozenset[str]:
    """Every language code this deployment serves content in.

    Read from ``PARLER_LANGUAGES`` rather than ``settings.LANGUAGES``
    because the callers are asking about TRANSLATION rows, and parler is
    what decides which of those exist. The two agree today (``el``,
    ``en``); parler is the one that would still be right if a UI
    language were added without translated content behind it.

    ``SITE_ID`` first, then parler's global bucket — which is the
    ``None`` key, NOT ``"default"``. Both hold the same shape (a tuple of
    ``{"code": ..., "fallbacks": ..., ...}`` entries) while
    ``PARLER_LANGUAGES["default"]`` is a single settings MAPPING —
    ``{'fallbacks': ['en'], 'hide_untranslated': False, 'code': 'el'}`` —
    so iterating it yields its keys and ``entry["code"]`` raises
    ``TypeError: string indices must be integers``. Measured on
    django-parler 2.4.

    Keyed on presence rather than truth, so an explicitly empty
    per-site list stays empty instead of silently inheriting the global
    one.
    """
    per_site = settings.PARLER_LANGUAGES.get(
        settings.SITE_ID, settings.PARLER_LANGUAGES.get(None, ())
    )
    return frozenset(entry["code"] for entry in per_site)


def store_language() -> str:
    """The language of the store this code runs for.

    ``Tenant.default_locale`` of the bound store, else the platform's
    ``LANGUAGE_CODE`` (no store is bound in the public schema). What a
    message uses when its reader stated no language of their own: a
    guest, an account that recorded none, and the store's own staff.
    """
    from tenant.membership import bound_tenant

    return (
        getattr(bound_tenant(), "default_locale", "") or settings.LANGUAGE_CODE
    )


def get_user_language(user) -> str:
    if user is None:
        return store_language()
    return getattr(user, "language_code", None) or store_language()


def get_order_language(order) -> str:
    if order is None:
        return store_language()
    return getattr(order, "language_code", None) or get_user_language(
        getattr(order, "user", None)
    )


def active_language_code() -> str:
    """The language the current request was made in.

    The code ``RequestLanguageMiddleware`` activated from the storefront's
    ``X-Language``, when this deployment serves it; the store's language
    otherwise. For rows that remember the language they were created in
    so a later email speaks it (``Order``, ``ProductAlert``).
    """
    candidate = (get_language() or "").split("-")[0].strip().lower()
    valid = {code for code, _name in settings.LANGUAGES}
    return candidate if candidate in valid else store_language()


def stated_request_language(request) -> str:
    """The language the caller asked for, or ``""`` when they asked for none
    this deployment serves (``X-Language``, then ``X-Locale``, then
    ``Accept-Language``)."""
    if request is None:
        return ""

    header = (
        request.headers.get("X-Language")
        or request.headers.get("X-Locale")
        or request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    )
    candidate = header.split(",")[0].split("-")[0].strip().lower()
    return candidate if candidate in available_language_codes() else ""


def resolve_request_language(request) -> str:
    return stated_request_language(request) or settings.LANGUAGE_CODE


def delete_translations_in(language_code: str, *model_labels: str):
    """A ``RunPython`` forward step that deletes the rows of *language_code*
    from the named parler translation models (``"app_label.ModelName"``).

    For a language the platform stopped serving: its rows would otherwise
    be indexed and listed by anything that reads every translation row.
    There is no way back; the content is gone."""

    def forward(apps, schema_editor):
        for label in model_labels:
            apps.get_model(label).objects.filter(
                language_code=language_code
            ).delete()

    return forward
