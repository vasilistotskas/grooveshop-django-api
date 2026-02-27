from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase
from django.utils import timezone
from knox.models import get_token_model

from core.middleware.channels import (
    TokenAuthMiddleware,
    TokenAuthMiddlewareStack,
    _cleanup_token,
    _renew_token,
    authenticate_token,
)

User = get_user_model()
Token = get_token_model()


class TestChannelsMiddleware(TransactionTestCase):
    @database_sync_to_async
    def create_test_user(self):
        return User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="password123",
        )

    @database_sync_to_async
    def create_test_token(
        self,
        user,
        token_key="test_token_key",
        digest="test_digest",
        expiry=None,
    ):
        if expiry is None:
            expiry = timezone.now() + timedelta(hours=1)
        elif isinstance(expiry, timedelta):
            expiry = timezone.now() + expiry
        token_instance = Token(
            user=user,
            token_key=token_key,
            digest=digest,
            expiry=expiry,
        )
        token_instance.save()
        return token_instance

    async def test_authenticate_token_invalid_input(self):
        result = await authenticate_token(None)
        self.assertIsInstance(result, AnonymousUser)

        result = await authenticate_token(123)
        self.assertIsInstance(result, AnonymousUser)

    async def test_authenticate_token_no_matching_tokens(self):
        result = await authenticate_token("non_existent_token")
        self.assertIsInstance(result, AnonymousUser)

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.hash_token")
    async def test_authenticate_token_hash_error(
        self, mock_hash_token, mock_logger
    ):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        await self.create_test_token(user, token_key=token_key)

        error = TypeError("Hash error")
        mock_hash_token.side_effect = error

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_any_call(
            "Error hashing WebSocket token: %s", error
        )

    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_digest_mismatch(
        self, mock_compare_digest, mock_hash_token
    ):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        await self.create_test_token(
            user, token_key=token_key, digest="correct_digest"
        )

        mock_hash_token.return_value = "wrong_digest"
        mock_compare_digest.return_value = False

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)

    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_success(
        self, mock_compare_digest, mock_hash_token
    ):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        await self.create_test_token(
            user, token_key=token_key, digest="correct_digest"
        )

        mock_hash_token.return_value = "correct_digest"
        mock_compare_digest.return_value = True

        result = await authenticate_token(token_string)

        self.assertEqual(result.id, user.id)

    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_inactive_user(
        self, mock_compare_digest, mock_hash_token
    ):
        user = await self.create_test_user()
        user.is_active = False
        await database_sync_to_async(user.save)()

        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        await self.create_test_token(
            user, token_key=token_key, digest="correct_digest"
        )

        mock_hash_token.return_value = "correct_digest"
        mock_compare_digest.return_value = True

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)

    @patch("core.middleware.channels.knox_settings")
    async def test_authenticate_token_with_auto_refresh(
        self, mock_knox_settings
    ):
        mock_knox_settings.AUTO_REFRESH = True

        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        expiry_time = timezone.now() + timedelta(hours=1)
        token = await self.create_test_token(
            user,
            token_key=token_key,
            digest="correct_digest",
            expiry=expiry_time,
        )

        with (
            patch("core.middleware.channels.hash_token") as mock_hash_token,
            patch(
                "core.middleware.channels.compare_digest"
            ) as mock_compare_digest,
            patch("core.middleware.channels._renew_token") as mock_renew_token,
        ):
            mock_hash_token.return_value = "correct_digest"
            mock_compare_digest.return_value = True

            result = await authenticate_token(token_string)

            self.assertEqual(result.id, user.id)
            mock_renew_token.assert_called_once_with(token)

    async def test_cleanup_token_expired(self):
        user = await self.create_test_user()
        expired_time = timezone.now() - timedelta(hours=1)
        token = await self.create_test_token(user, expiry=expired_time)

        result = await database_sync_to_async(_cleanup_token)(token)

        self.assertTrue(result)
        with self.assertRaises(Token.DoesNotExist):
            await database_sync_to_async(Token.objects.get)(pk=token.pk)

    async def test_cleanup_token_not_expired(self):
        user = await self.create_test_user()
        future_time = timezone.now() + timedelta(hours=1)
        token = await self.create_test_token(user, expiry=future_time)

        result = await database_sync_to_async(_cleanup_token)(token)

        self.assertFalse(result)
        existing_token = await database_sync_to_async(Token.objects.get)(
            pk=token.pk
        )
        self.assertEqual(existing_token.pk, token.pk)

    async def test_cleanup_token_no_expiry(self):
        user = await self.create_test_user()
        token = await self.create_test_token(user, expiry=None)

        result = await database_sync_to_async(_cleanup_token)(token)

        self.assertFalse(result)
        existing_token = await database_sync_to_async(Token.objects.get)(
            pk=token.pk
        )
        self.assertEqual(existing_token.pk, token.pk)

    @patch("core.middleware.channels.knox_settings")
    async def test_renew_token_significant_delta(self, mock_knox_settings):
        mock_knox_settings.TOKEN_TTL = timedelta(hours=24)
        mock_knox_settings.MIN_REFRESH_INTERVAL = 300

        user = await self.create_test_user()
        old_expiry = timezone.now() + timedelta(hours=1)
        token = await self.create_test_token(user, expiry=old_expiry)

        await database_sync_to_async(_renew_token)(token)

        await database_sync_to_async(token.refresh_from_db)()

        self.assertGreater(token.expiry, old_expiry)

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.knox_settings")
    async def test_renew_token_small_delta(
        self, mock_knox_settings, mock_logger
    ):
        mock_knox_settings.TOKEN_TTL = timedelta(seconds=1)
        mock_knox_settings.MIN_REFRESH_INTERVAL = 300

        user = await self.create_test_user()
        old_expiry = timezone.now() + timedelta(hours=1)
        token = await self.create_test_token(user, expiry=old_expiry)

        await database_sync_to_async(_renew_token)(token)

        await database_sync_to_async(token.refresh_from_db)()

        self.assertEqual(token.expiry, old_expiry)

    async def test_token_auth_middleware_with_access_token(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"access_token=test_access_token&other_param=value",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_auth.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_auth.assert_called_once_with("test_access_token")
            self.assertEqual(scope["user"], mock_user)

    async def test_token_auth_middleware_with_session_token(self):
        """session_token is not supported; only access_token is used."""

        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"session_token=test_session_token",
            "user": None,
        }

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_token_auth_middleware_with_user_id_ignored(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"query_string": b"user_id=123", "user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_token_auth_middleware_access_token_priority(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"access_token=access_token_value&session_token=session_token_value",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_auth.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_auth.assert_called_once_with("access_token_value")

    async def test_token_auth_middleware_no_auth(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"query_string": b"other_param=value", "user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_token_auth_middleware_no_query_string(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)

    async def test_token_auth_middleware_anonymous_result(self):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"access_token=invalid_token",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_auth.return_value = AnonymousUser()

            await middleware(scope, AsyncMock(), AsyncMock())

            self.assertIsInstance(scope["user"], AnonymousUser)

    def test_token_auth_middleware_stack(self):
        mock_inner = MagicMock()
        result = TokenAuthMiddlewareStack(mock_inner)

        self.assertIsInstance(result, TokenAuthMiddleware)
        self.assertEqual(result.inner, mock_inner)

    async def test_authenticate_token_expired_cleanup(self):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        expired_time = timezone.now() - timedelta(hours=1)
        token = await self.create_test_token(
            user, token_key=token_key, expiry=expired_time
        )

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)

        with self.assertRaises(Token.DoesNotExist):
            await database_sync_to_async(Token.objects.get)(pk=token.pk)

    async def test_token_auth_middleware_logging_flow(self):
        """Verify access_token is used and user is set in scope."""

        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"access_token=test_token&session_token=session_token",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_auth.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_auth.assert_called_once_with("test_token")
            self.assertEqual(scope["user"], mock_user)
