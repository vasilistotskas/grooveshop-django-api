"""Tests for the Channels WebSocket auth middleware.

The ``access_token`` / ``authenticate_token`` / ``_renew_token`` /
``_cleanup_token`` path was removed in favour of the ticket-only flow.
These tests cover:
- ``authenticate_ticket``: atomic GETDEL, Knox live-token check, user
  active/inactive, missing user, empty/missing ticket.
- ``TokenAuthMiddleware.__call__``: ticket path, no-ticket → AnonymousUser.
- ``TokenAuthMiddlewareStack``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase
from knox.models import get_token_model

from core.middleware.channels import (
    TokenAuthMiddleware,
    TokenAuthMiddlewareStack,
    authenticate_ticket,
)

User = get_user_model()
Token = get_token_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_default_cache():
    """Drop the THREAD-LOCAL default-cache instance before each test.

    These tests are async, so they resolve the cache proxy on the
    event-loop thread, whose thread-local ``CacheHandler`` entry may
    have been materialized inside another test's
    ``override_settings(CACHES=LocMem)`` window (the dashboard caching
    tests do exactly that). Dropping it forces re-resolution.

    Re-resolution alone is NOT enough, which is why this used to flake
    7 tests: ``tests/conftest.py`` sets ``settings.CACHES`` to LocMem,
    so whatever gets rebuilt here is LocMem too — and LocMem's
    ``_cache`` is a plain ``OrderedDict`` with no ``get_client``. Pass
    or fail then depended on whether a thread-local entry happened to
    exist, i.e. on which other tests shared the xdist worker. The
    patcher below no longer depends on the concrete backend.
    """
    from django.core.cache import caches

    try:
        del caches._connections.default
    except AttributeError:
        pass
    yield


def _make_getdel_patcher(return_value):
    """Return a context manager that patches the raw Redis GETDEL call."""
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = return_value
    # ``create=True`` because the attribute only exists on the
    # Redis-backed CustomCache. Under the test settings the resolved
    # backend may be LocMem, where ``_cache`` is an OrderedDict and the
    # attribute is absent — patching then raised AttributeError instead
    # of testing anything. What is under test is the middleware's use of
    # the getdel round-trip, which the mock provides either way, so the
    # concrete backend is irrelevant here.
    return patch(
        "core.middleware.channels.cache._cache.get_client",
        return_value=mock_redis,
        create=True,
    )


# ---------------------------------------------------------------------------
# authenticate_ticket unit tests
# ---------------------------------------------------------------------------


class TestAuthenticateTicket(TransactionTestCase):
    @database_sync_to_async
    def _create_user(self, *, active=True):
        user = User.objects.create_user(
            username=f"ws_test_{User.objects.count()}",
            email=f"ws_{User.objects.count()}@example.com",
            password="hunter2",
        )
        if not active:
            user.is_active = False
            user.save(update_fields=["is_active"])
        return user

    @database_sync_to_async
    def _create_token(self, user):
        token_obj, _ = Token.objects.create(user=user)
        return token_obj

    # -- empty / None ticket -------------------------------------------------

    async def test_empty_string_returns_anonymous(self):
        result = await authenticate_ticket("")
        self.assertIsInstance(result, AnonymousUser)

    async def test_none_ticket_returns_anonymous(self):
        result = await authenticate_ticket(None)  # type: ignore[arg-type]
        self.assertIsInstance(result, AnonymousUser)

    # -- GETDEL returns None (ticket not in cache or already consumed) -------

    async def test_missing_cache_entry_returns_anonymous(self):
        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(None),
        ):
            result = await authenticate_ticket("abc")
        self.assertIsInstance(result, AnonymousUser)

    # -- GETDEL returns garbled value ----------------------------------------

    async def test_invalid_cache_value_returns_anonymous(self):
        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(b"not-an-int"),
        ):
            result = await authenticate_ticket("abc")
        self.assertIsInstance(result, AnonymousUser)

    # -- user does not exist -------------------------------------------------

    async def test_nonexistent_user_returns_anonymous(self):
        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(b"99999999"),
        ):
            result = await authenticate_ticket("abc")
        self.assertIsInstance(result, AnonymousUser)

    # -- inactive user -------------------------------------------------------

    async def test_inactive_user_returns_anonymous(self):
        user = await self._create_user(active=False)
        await self._create_token(user)

        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(str(user.pk).encode()),
        ):
            result = await authenticate_ticket("abc")
        self.assertIsInstance(result, AnonymousUser)

    # -- no live Knox tokens -------------------------------------------------

    async def test_no_knox_token_returns_anonymous(self):
        user = await self._create_user()
        # Deliberately do NOT create a Knox token.
        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(str(user.pk).encode()),
        ):
            result = await authenticate_ticket("abc")
        self.assertIsInstance(result, AnonymousUser)

    # -- happy path ----------------------------------------------------------

    async def test_valid_ticket_returns_user(self):
        user = await self._create_user()
        await self._create_token(user)

        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value="ws:ticket:abc",
            ),
            _make_getdel_patcher(str(user.pk).encode()),
        ):
            result = await authenticate_ticket("abc")

        self.assertEqual(result.pk, user.pk)

    # -- GETDEL is called with the prefixed key ------------------------------

    async def test_getdel_uses_prefixed_key(self):
        user = await self._create_user()
        await self._create_token(user)

        prefixed = "redis:1:ws:ticket:myticket"
        mock_redis = MagicMock()
        mock_redis.getdel.return_value = str(user.pk).encode()

        with (
            patch(
                "core.middleware.channels.cache.make_and_validate_key",
                return_value=prefixed,
            ),
            patch(
                "core.middleware.channels.cache._cache.get_client",
                return_value=mock_redis,
                # See _make_getdel_patcher: the attribute exists only on
                # the Redis-backed CustomCache, and the resolved backend
                # here may be LocMem.
                create=True,
            ),
        ):
            await authenticate_ticket("myticket")

        mock_redis.getdel.assert_called_once_with(prefixed)


# ---------------------------------------------------------------------------
# TokenAuthMiddleware.__call__
# ---------------------------------------------------------------------------


class TestTokenAuthMiddleware(TransactionTestCase):
    async def _middleware(self):
        async def inner(scope, receive, send):
            pass

        return TokenAuthMiddleware(inner)

    async def test_ticket_param_calls_authenticate_ticket(self):
        middleware = await self._middleware()
        scope = {"query_string": b"ticket=myticket", "user": None}

        mock_user = MagicMock()
        mock_user.is_anonymous = False

        with patch(
            "core.middleware.channels.authenticate_ticket",
            AsyncMock(return_value=mock_user),
        ) as mock_auth:
            await middleware(scope, AsyncMock(), AsyncMock())

        # The tenant is passed so the lookup runs in the connecting
        # tenant's schema — the ticket cache key and the knox token
        # table are both schema-scoped, and database_sync_to_async
        # would otherwise run on a thread bound to whatever schema the
        # previous request left behind.
        mock_auth.assert_called_once_with("myticket", scope.get("tenant"))
        self.assertEqual(scope["user"], mock_user)

    async def test_no_ticket_produces_anonymous(self):
        middleware = await self._middleware()
        scope = {"query_string": b"other=value", "user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_access_token_param_is_ignored(self):
        """The legacy access_token query param is no longer accepted."""
        middleware = await self._middleware()
        scope = {"query_string": b"access_token=legacytoken", "user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_empty_query_string_produces_anonymous(self):
        middleware = await self._middleware()
        scope = {"user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    def test_token_auth_middleware_stack(self):
        mock_inner = MagicMock()
        result = TokenAuthMiddlewareStack(mock_inner)

        self.assertIsInstance(result, TokenAuthMiddleware)
        self.assertEqual(result.inner, mock_inner)
