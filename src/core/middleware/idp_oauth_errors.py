"""A refused ``resource`` at the IdP's token endpoint is an OAuth error.

allauth validates the ``resource`` parameters (RFC 8707) of the token
endpoint with ``get_resources``, which reports a refusal as a
Django ``ValidationError``, but those views handle only oauthlib's
``OAuth2Error``. So a malformed resource, or one
``agent.oidc.AgentOIDCAdapter.validate_resource_uris`` refuses (the staff
issuer's token request with none, or naming no store), escaped as an
uncoded 500. RFC 8707 section 2 defines ``invalid_target`` for exactly
this: "The requested resource is invalid, missing, unknown, or malformed."

Any ``ValidationError`` on that route is mapped, because
``get_resources`` is the only code that raises one there today
(allauth's ``views.py``: ``TokenView.post``, the ``get_resources`` call
before the oauthlib server runs); recheck this on every allauth upgrade.
The device-code endpoint is not served (``core.views.device_flow_not_offered``).

This answers it in OAuth's body, as the strict-cache 503 does for the same
routes (``core.middleware.strict_cache``). The authorize view handles the
same exception itself, with an error page, so it is not matched here.
"""

from __future__ import annotations

from collections.abc import Callable

from allauth.idp.oidc.internal.resources import InvalidTargetError
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse

#: allauth's URL names (``allauth.idp.oidc.urls``, namespace ``idp:oidc``)
#: of the endpoint that calls ``get_resources`` and does not catch it.
_IDP_NAMESPACES = ["idp", "oidc"]
_RESOURCE_ENDPOINT = "token"


class IdpResourceErrorMiddleware:
    """Answer a ``ValidationError`` from an IdP token endpoint's resource
    validation with ``invalid_target``. Any other error is left to Django."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(
        self, request: HttpRequest, exception: Exception
    ) -> HttpResponse | None:
        match = request.resolver_match
        if (
            not isinstance(exception, ValidationError)
            or match is None
            or match.namespaces != _IDP_NAMESPACES
            or match.url_name != _RESOURCE_ENDPOINT
        ):
            return None
        error = InvalidTargetError(description=" ".join(exception.messages))
        return JsonResponse(dict(error.twotuples), status=error.status_code)
