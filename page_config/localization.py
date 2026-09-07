"""Which locale a public page-config request is answered in.

Page sections and navigation menus are OPERATOR CONTENT held in JSON,
not parler translations, so the usual "ship every translation, let the
client pick" route is not open to them: ``props`` is one field mixing
layout configuration with customer-facing copy, and duplicating the
whole blob per language would let ``columns`` drift between them.

They are resolved server-side from an explicit ``?locale=`` instead of
from ``Accept-Language``. The storefront proxies these endpoints and
already knows the locale it is rendering (``event.context.locale``); it
does NOT forward ``Accept-Language`` — it sends its own ``X-Language``
header, which nothing in the request path activates — so a header-based
answer would silently serve the default locale. An explicit parameter
also keeps the caches honest: both sides key on the value they passed.
"""

from __future__ import annotations

from django.conf import settings

from core.utils.i18n import available_language_codes

LOCALE_PARAM = "locale"


def requested_locale(request) -> str:
    """The available language code ``request`` asked for.

    Anything unknown falls back to the default locale rather than 400:
    these are public read routes and an unrecognised locale means "serve
    the store's own language", not "the request is broken".
    """
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE
    if request is None:
        return default
    candidate = (request.query_params.get(LOCALE_PARAM) or "").strip().lower()
    return candidate if candidate in available_language_codes() else default
