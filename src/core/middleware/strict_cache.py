"""The answer to a request whose security store is unreachable.

A key under ``core.cache_backend.STRICT_KEY_PREFIXES`` (or a ``count_in_window`` /
``add`` / ``incr``) raises ``REDIS_UNAVAILABLE`` instead of lying when Redis
cannot be reached: a rate limit that cannot count, a TOTP code whose replay
cannot be checked, an IdP code or a ticket that cannot be stored or spent.
The request is refused. This module is the one place that says how, so every
route refuses the same way, in the shape its client reads:

- allauth's headless API: allauth's envelope,
  ``{"status": 503, "errors": [{"code", "message"}]}``;
- the IdP's OAuth endpoints (``identity/o/api/``): OAuth's error body,
  ``{"error": "temporarily_unavailable", "error_description"}`` (RFC 6749
  names that code for exactly this);
- a DRF view: the API error contract, ``{"detail", "code"}``
  (``docs/error-contract.md``);
- any other page (allauth's ``accounts/`` pages, the IdP's authorize and
  device pages, the admin): an HTML page.

Always 503 with the code ``temporarily_unavailable``. Never a 429 (nothing
was sent too often, and no ``Retry-After`` can be computed from a history
that cannot be read) and never a 500 (uncoded, and read as a crash).

``StrictCacheUnavailableMiddleware`` answers what a view raises. A
middleware that reads a strict key itself (``core.middleware.
allauth_ratelimit``) calls ``unavailable_response``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, ResolverMatch, resolve
from rest_framework.views import APIView

from core.cache_backend import (
    REDIS_UNAVAILABLE,
    STRICT_CACHE_UNAVAILABLE_CODE,
    STRICT_CACHE_UNAVAILABLE_MESSAGE,
    BurstLogger,
)

logger = logging.getLogger(__name__)

STATUS = 503

#: The URL namespace of allauth's headless API (``allauth.headless.urls``).
_HEADLESS_NAMESPACE = "headless"
#: The IdP's machine-to-machine endpoints (``allauth.idp.oidc.urls``
#: ``_api_urlpatterns``): token, revoke, device code, introspection,
#: userinfo, client registration. Its other routes are pages.
_IDP_NAMESPACE = "idp"
_IDP_API_ROUTE = "identity/o/api/"

_refused = BurstLogger(
    "Request to %s REFUSED: a security store is unreachable (Redis), and it "
    "fails closed: %s (%s similar refusal(s) suppressed in the last %ss)",
    level=logging.ERROR,
    target=logger,
)


def _match(request: HttpRequest) -> ResolverMatch | None:
    """The view the request reached, resolved here when a middleware asks
    before Django resolved it."""
    if request.resolver_match is not None:
        return request.resolver_match
    try:
        return resolve(request.path_info, getattr(request, "urlconf", None))
    except Resolver404:
        return None


def _is_drf(match: ResolverMatch) -> bool:
    view_class = getattr(match.func, "cls", None)
    return isinstance(view_class, type) and issubclass(view_class, APIView)


def unavailable_response(
    request: HttpRequest, exc: BaseException
) -> HttpResponse:
    """The 503 for *request*, in the shape of the route it reached."""
    # The traceback names the key's caller: the middleware answers any
    # Redis outage a view raises, strict keys or not.
    _refused.report(request.path_info, exc, exc_info=exc)
    message = str(STRICT_CACHE_UNAVAILABLE_MESSAGE)
    match = _match(request)
    namespaces = match.namespaces if match is not None else []
    if _HEADLESS_NAMESPACE in namespaces:
        return JsonResponse(
            {
                "status": STATUS,
                "errors": [
                    {"code": STRICT_CACHE_UNAVAILABLE_CODE, "message": message}
                ],
            },
            status=STATUS,
        )
    if (
        match is not None
        and _IDP_NAMESPACE in namespaces
        and match.route.startswith(_IDP_API_ROUTE)
    ):
        return JsonResponse(
            {
                "error": STRICT_CACHE_UNAVAILABLE_CODE,
                "error_description": message,
            },
            status=STATUS,
        )
    if match is not None and _is_drf(match):
        return JsonResponse(
            {"detail": message, "code": STRICT_CACHE_UNAVAILABLE_CODE},
            status=STATUS,
        )
    return render(
        request,
        "503.html",
        {"message": message, "code": STRICT_CACHE_UNAVAILABLE_CODE},
        status=STATUS,
    )


class StrictCacheUnavailableMiddleware:
    """Answer a view that could not reach a strict key with the coded 503
    (``unavailable_response``). Any other error is left to Django."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(
        self, request: HttpRequest, exception: Exception
    ) -> HttpResponse | None:
        if isinstance(exception, REDIS_UNAVAILABLE):
            return unavailable_response(request, exception)
        return None
