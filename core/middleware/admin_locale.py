"""Force-activate Greek for /admin/ when no explicit language is set.

Background:
    `LANGUAGE_CODE = "el"` is already the global default, but
    `LocaleMiddleware` honors the browser's `Accept-Language` header
    first. The storefront talks to international users so honoring it
    everywhere is correct, but the admin is operated by the shop staff
    who all read Greek — and the Greek translation is now the canonical
    one. We force `el` on `/admin/` requests when the operator hasn't
    explicitly chosen a language via the unfold language switcher
    (which writes the `django_language` cookie).
"""

from __future__ import annotations

from django.conf import settings
from django.utils import translation


class AdminDefaultGreekMiddleware:
    """Activate Greek for admin requests with no language cookie.

    Place this *after* `django.middleware.locale.LocaleMiddleware` so the
    user's explicit choice (cookie) wins. Anonymous staff hitting
    `/admin/login/` for the first time will see Greek.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if path.startswith("/admin/"):
            cookie_name = settings.LANGUAGE_COOKIE_NAME
            if not request.COOKIES.get(cookie_name):
                translation.activate("el")
                request.LANGUAGE_CODE = "el"
        return self.get_response(request)
