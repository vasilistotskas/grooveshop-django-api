"""Activate the request's language for the storefront API.

Django's ``LocaleMiddleware`` cannot do this on its own. With
``i18n_patterns(prefix_default_language=False)`` (``core/urls.py``) it
activates ``settings.LANGUAGE_CODE`` for every path that carries no
language prefix (``django/middleware/locale.py`` ``process_request``:
``if not language_from_path and i18n_patterns_used and not
prefixed_default_language: language = settings.LANGUAGE_CODE``). The
storefront calls unprefixed ``/api/v1/...`` and ``/_allauth/...`` and
states the visitor's language in ``X-Language``, so every response —
validation errors, ``detail`` messages, choice labels and parler
fields read in the active language — came back in Greek on English
pages.

For those paths the language is ``resolve_request_language(request)``
(``X-Language``, then ``X-Locale``, then ``Accept-Language``, clamped
to the languages this deployment serves). Everything else — the
admin, which ``AdminDefaultGreekMiddleware`` then pins, ``/accounts/``,
``/i18n/``, a path with an explicit ``/en/`` prefix — keeps Django's
own rule unchanged.
"""

from __future__ import annotations

from django.middleware.locale import LocaleMiddleware
from django.utils import translation
from django.utils.cache import patch_vary_headers

from core.utils.i18n import resolve_request_language

#: Unprefixed paths answered in the caller's stated language. A path with
#: a language prefix (``/en/api/v1/...``) does not start with these, so
#: its explicit prefix keeps winning through ``LocaleMiddleware``.
REQUEST_LANGUAGE_PATH_PREFIXES = ("/api/", "/_allauth/")

#: The request headers ``resolve_request_language`` reads, besides
#: ``Accept-Language`` (which ``LocaleMiddleware.process_response``
#: already adds to ``Vary``).
REQUEST_LANGUAGE_HEADERS = ("X-Language", "X-Locale")


def uses_request_language(request) -> bool:
    return request.path_info.startswith(REQUEST_LANGUAGE_PATH_PREFIXES)


class RequestLanguageMiddleware(LocaleMiddleware):
    """``LocaleMiddleware`` with the API answered in the caller's language.

    A subclass rather than a second middleware beside it, so exactly one
    component decides the language of any request, and the parent's
    ``process_response`` still sets ``Content-Language`` and
    ``Vary: Accept-Language``.

    The language is switched in ``process_view``, AFTER URL resolution,
    not in ``process_request``. Resolution itself reads the active
    language: ``LocalePrefixPattern.language_prefix`` is ``""`` only
    while ``get_language()`` is ``LANGUAGE_CODE``
    (``django/urls/resolvers.py``), so an unprefixed ``/api/v1/...``
    resolved under ``en`` expects ``/en/`` and 404s. ``process_request``
    therefore stays the parent's (the default language, which is what
    the unprefixed routes are registered under), and Django calls
    ``process_view`` once the view is known and before it runs
    (``django/core/handlers/base.py`` ``_get_response``) — which covers
    the view, its serializers and the response rendering.

    ``request.LANGUAGE_CODE`` is set as well as the active language:
    Django's cache key suffix reads it before ``get_language()``
    (``django/utils/cache.py`` ``_i18n_cache_key_suffix``), and
    ``cache_page`` runs inside the view
    (``core.utils.views.cache_methods``), so every cached entry is keyed
    on the language its body was rendered in.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        if uses_request_language(request):
            translation.activate(resolve_request_language(request))
            request.LANGUAGE_CODE = translation.get_language()

    def process_response(self, request, response):
        response = super().process_response(request, response)
        if uses_request_language(request):
            # The body was chosen by these headers, so a shared cache
            # that honours Vary must key on them too (RFC 9110 §12.5.5).
            patch_vary_headers(response, REQUEST_LANGUAGE_HEADERS)
            translation.deactivate()
        return response
