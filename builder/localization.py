"""Which locale a public builder read is answered in.

A builder document is OPERATOR CONTENT held in JSON with a per-language
overlay, not parler translations, so the usual "ship every translation,
let the client pick" route is not open to it: the structure is one, and
only the overlay varies by language.

It is resolved server-side from an explicit ``?locale=``. The
storefront proxies these endpoints and already knows the locale it is
rendering (``event.context.locale``), which it passes both as this
parameter and as ``X-Language`` — the header the request is answered in
(``core.middleware.locale.RequestLanguageMiddleware``). The explicit
parameter selects the operator content and keeps the caches honest:
both sides key on the value they passed.
"""

from __future__ import annotations

from django.conf import settings

from core.utils.i18n import available_language_codes

LOCALE_PARAM = "locale"


def resolve_locale(candidate: str | None) -> str:
    """The available language code *candidate* names; anything unknown,
    blank or missing falls back to the default locale rather than failing:
    these reads mean "serve the store's own language", not "the request is
    broken"."""
    return (
        code
        if (code := (candidate or "").strip().lower())
        in available_language_codes()
        else settings.PARLER_DEFAULT_LANGUAGE_CODE
    )


def requested_locale(request) -> str:
    """The available language code ``request`` asked for (``?locale=``)."""
    if request is None:
        return settings.PARLER_DEFAULT_LANGUAGE_CODE
    return resolve_locale(request.query_params.get(LOCALE_PARAM))
