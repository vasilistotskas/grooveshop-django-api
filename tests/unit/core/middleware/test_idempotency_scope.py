"""Whose ``Idempotency-Key`` a request carries: the shopper's, never the
connection's (``core/middleware/idempotency.py``).

A key used to belong to the session user, else the session, else the
client address. A Bearer token is resolved by DRF inside the view, so a
signed-in shopper looked anonymous to the middleware and was scoped by
address, and so was every anonymous cart call. One shopper reaching
Django from two addresses (two agent gateway pods, a phone changing
networks) had a retry run twice.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from knox.models import get_token_model
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.middleware.idempotency import (
    IDEMPOTENCY_HEADER,
    MAX_KEYS_PER_CALLER,
    IdempotencyMiddleware,
)
from tests.utils.idempotency import enter, plain_view
from user.factories.account import UserAccountFactory

AuthToken = get_token_model()

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "idempotency-scope-test",
    }
}


class _StoreView(APIView):
    """An API view with the default authenticators, as store views are."""

    permission_classes = [AllowAny]

    def post(self, request):
        return Response({"ok": True}, status=201)


class _TakesNoCredentials(_StoreView):
    authentication_classes = []


store_view = _StoreView.as_view()
takes_no_credentials = _TakesNoCredentials.as_view()


def _bearer(user=None) -> dict[str, str]:
    _token, raw = AuthToken.objects.create(user or UserAccountFactory())
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@override_settings(CACHES=_LOCMEM)
class IdempotencyScopeTest(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.mw = IdempotencyMiddleware(lambda r: JsonResponse({"ok": True}))

    def _request(
        self,
        key: str = "key-1",
        *,
        ip: str = "203.0.113.5",
        user: Any = None,
        **headers: str,
    ):
        req = RequestFactory().post("/api/v1/cart/item", **headers)
        req.META[IDEMPOTENCY_HEADER] = key
        req.META["REMOTE_ADDR"] = ip
        req.user = user or AnonymousUser()
        req.session = None
        return req

    def _send(self, view: Any = store_view, **kwargs: Any) -> HttpResponse:
        req = self._request(**kwargs)
        early = enter(self.mw, req, view)
        if early is not None:
            return early
        return self.mw.process_response(
            req, JsonResponse({"ok": True}, status=201)
        )

    def _replayed(self, view: Any = store_view, **kwargs: Any) -> bool:
        response = enter(self.mw, self._request(**kwargs), view)
        return (
            response is not None and response.get("Idempotent-Replay") == "true"
        )

    def test_a_signed_in_shopper_keeps_their_key_on_another_address(self):
        bearer = _bearer()

        self._send(ip="203.0.113.5", **bearer)

        assert self._replayed(ip="198.51.100.9", **bearer)

    def test_two_shoppers_never_share_a_key(self):
        self._send(**_bearer())

        assert not self._replayed(**_bearer())

    def test_a_session_user_is_the_principal_outside_drf(self):
        user = UserAccountFactory()

        self._send(plain_view, ip="203.0.113.5", user=user)

        assert self._replayed(plain_view, ip="198.51.100.9", user=user)

    def test_a_view_that_takes_no_credentials_has_no_principal(self):
        """Scoped the way the view sees the caller: anonymous, so by
        address here, whatever token the request carries."""
        bearer = _bearer()

        self._send(takes_no_credentials, ip="203.0.113.5", **bearer)

        assert not self._replayed(
            takes_no_credentials, ip="198.51.100.9", **bearer
        )

    def test_refused_credentials_are_not_guarded(self):
        """The view refuses them too; nothing is reserved or replayed."""
        refused = {"HTTP_AUTHORIZATION": "Bearer not-a-token"}
        first = self._request(**refused)

        assert enter(self.mw, first, store_view) is None
        assert not hasattr(first, "_idempotency_cache_key")
        assert enter(self.mw, self._request(**refused), store_view) is None

    def test_an_anonymous_cart_keeps_its_key_on_another_address(self):
        cart = {"HTTP_X_CART_ID": str(uuid4())}

        self._send(ip="203.0.113.5", **cart)

        assert self._replayed(ip="198.51.100.9", **cart)

    def test_two_carts_never_share_a_key(self):
        self._send(HTTP_X_CART_ID=str(uuid4()))

        assert not self._replayed(HTTP_X_CART_ID=str(uuid4()))

    def test_a_cart_token_keeps_its_key_on_another_address(self):
        """The agent gateway reaches a signed-in shopper's cart with a
        cart token and no login."""
        token = {"HTTP_X_CART_TOKEN": "a-cart-token"}

        self._send(ip="203.0.113.5", **token)

        assert self._replayed(ip="198.51.100.9", **token)

    def test_new_carts_buy_no_new_budget(self):
        """The budget is the caller's: anyone can mint a cart."""
        for n in range(MAX_KEYS_PER_CALLER + 20):
            self._send(key=f"key-{n}", HTTP_X_CART_ID=str(uuid4()))
        late = {"key": "late", "HTTP_X_CART_ID": str(uuid4())}

        self._send(**late)

        assert not self._replayed(**late), (
            "a key past the caller's budget was stored: a fresh cart per "
            "request is a fresh budget"
        )
