"""Refuse credential changes on a store's shared demo account.

`/_allauth/` bypasses DRF entirely, so a permission class cannot reach
these endpoints — the same reason `allauth_ratelimit` is a middleware.

The paths below are the ones that can cost the shared account its
login: change the password, add or remove an email address, or enrol a
second factor. Everything else about the account stays writable, so a
prospect can still browse orders, edit addresses, favourite things and
place an order — which is the point of handing them the account.

Password RESET is not here: it is unauthenticated, so this middleware
cannot tell whose account it is. It is refused in
`TenantAccountAdapter.set_password` instead, which both flows go
through.

On a normal store `demo_account_emails()` is empty and this returns
before it looks at anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from core.demo_account import demo_account_emails

logger = logging.getLogger(__name__)

_ALLAUTH_ACCOUNT = "/_allauth/app/v1/account/"

#: Suffixes under `_ALLAUTH_ACCOUNT`, matched as prefixes so that
#: `authenticators/totp` and `authenticators/webauthn` are both covered.
_GUARDED = (
    "password/change",
    "email",
    "phone",
    "authenticators",
)

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class DemoAccountGuardMiddleware:
    """Block credential mutations for a shared demo login."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self._is_blocked(request):
            return JsonResponse(
                {
                    "status": 403,
                    "errors": [
                        {
                            "code": "demo_account_locked",
                            "message": (
                                "This is a shared demo account, so its "
                                "sign-in details cannot be changed. "
                                "Everything else is yours to try."
                            ),
                        }
                    ],
                },
                status=403,
            )
        return self.get_response(request)

    def _is_blocked(self, request: HttpRequest) -> bool:
        if request.method not in _UNSAFE:
            return False
        path = request.path
        if not path.startswith(_ALLAUTH_ACCOUNT):
            return False
        suffix = path[len(_ALLAUTH_ACCOUNT) :]
        if not any(suffix.startswith(guarded) for guarded in _GUARDED):
            return False

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        email = str(getattr(user, "email", "") or "").strip().lower()
        if not email:
            return False
        # Read the settings LAST: this is a per-request DB lookup, and
        # by here the request is already a credential mutation by a
        # signed-in user, which is rare.
        if email not in demo_account_emails():
            return False

        logger.info(
            "Refused a credential change on the shared demo account",
            extra={"path": path, "method": request.method},
        )
        return True
