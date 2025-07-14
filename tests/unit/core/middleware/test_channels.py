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
    get_user,
)

User = get_user_model()
Token = get_token_model()


class TestChannelsMiddleware(TransactionTestCase):
    @database_sync_to_async
    def create_test_user(self):
        return User.objects.create_user(
            username="testuser", email="test@test.com", password="password123"
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
            user=user, token_key=token_key, digest=digest, expiry=expiry
        )
        token_instance.save()
        return token_instance

    async def test_get_user_exists(self):
        user = await self.create_test_user()
        result_user = await get_user(user.id)

        self.assertEqual(result_user.id, user.id)
        self.assertEqual(result_user.username, "testuser")

    async def test_get_user_not_exists(self):
        result_user = await get_user(99999)

        self.assertTrue(result_user.is_anonymous)
        self.assertIsInstance(result_user, AnonymousUser)

    @patch("core.middleware.channels.logger")
    async def test_authenticate_token_invalid_input(self, mock_logger):
        result = await authenticate_token(None)
        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_called_with(
            "Token has no strip method, returning AnonymousUser"
        )

        mock_logger.reset_mock()
        result = await authenticate_token(123)
        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_called_with(
            "Token has no strip method, returning AnonymousUser"
        )

    @patch("core.middleware.channels.logger")
    async def test_authenticate_token_no_matching_tokens(self, mock_logger):
        result = await authenticate_token("non_existent_token")

        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_any_call("Authenticating token: non_existe...")
        mock_logger.debug.assert_any_call(
            "No valid token found, returning AnonymousUser"
        )

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.hash_token")
    async def test_authenticate_token_hash_error(
        self, mock_hash_token, mock_logger
    ):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        await self.create_test_token(user, token_key=token_key)

        mock_hash_token.side_effect = TypeError("Hash error")

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_any_call(f"Found token with key: {token_key}")
        mock_logger.debug.assert_any_call("Error hashing token: Hash error")

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_digest_mismatch(
        self, mock_compare_digest, mock_hash_token, mock_logger
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
        mock_logger.debug.assert_any_call("Token digest mismatch")

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_success(
        self, mock_compare_digest, mock_hash_token, mock_logger
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
        mock_logger.debug.assert_any_call("Token digest match")
        mock_logger.debug.assert_any_call(
            f"Authenticated user: {user.username}"
        )

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.hash_token")
    @patch("core.middleware.channels.compare_digest")
    async def test_authenticate_token_inactive_user(
        self, mock_compare_digest, mock_hash_token, mock_logger
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
        mock_logger.debug.assert_any_call("Token digest match")
        mock_logger.debug.assert_any_call("Authenticated user: AnonymousUser")

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.knox_settings")
    async def test_authenticate_token_with_auto_refresh(
        self, mock_knox_settings, mock_logger
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
            mock_logger.debug.assert_any_call("Auto refreshing token")
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

    @patch("core.middleware.channels.logger")
    @patch("core.middleware.channels.knox_settings")
    async def test_renew_token_significant_delta(
        self, mock_knox_settings, mock_logger
    ):
        mock_knox_settings.TOKEN_TTL = timedelta(hours=24)
        mock_knox_settings.MIN_REFRESH_INTERVAL = 300

        user = await self.create_test_user()
        old_expiry = timezone.now() + timedelta(hours=1)
        token = await self.create_test_token(user, expiry=old_expiry)

        await database_sync_to_async(_renew_token)(token)

        await database_sync_to_async(token.refresh_from_db)()

        self.assertGreater(token.expiry, old_expiry)
        mock_logger.debug.assert_called()

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

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_with_access_token(self, mock_logger):
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
            mock_logger.debug.assert_any_call(
                "TokenAuthMiddleware Token found, authenticating"
            )
            mock_logger.debug.assert_any_call("User authenticated: testuser")

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_with_session_token(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"session_token=test_session_token",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_auth.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_auth.assert_called_once_with("test_session_token")
            self.assertEqual(scope["user"], mock_user)

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_with_user_id(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"query_string": b"user_id=123", "user": None}

        with patch("core.middleware.channels.get_user") as mock_get_user:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_get_user.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_get_user.assert_called_once_with(123)
            self.assertEqual(scope["user"], mock_user)
            mock_logger.debug.assert_any_call(
                "No token found, but user_id is present: 123"
            )
            mock_logger.debug.assert_any_call("User retrieved by ID: testuser")

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_access_token_priority(
        self, mock_logger
    ):
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

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_no_auth(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"query_string": b"other_param=value", "user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)
        mock_logger.debug.assert_any_call(
            "TokenAuthMiddleware Token not found and no user_id"
        )

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_no_query_string(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"user": None}

        await middleware(scope, AsyncMock(), AsyncMock())

        self.assertIsInstance(scope["user"], AnonymousUser)
        mock_logger.debug.assert_any_call("TokenAuthMiddleware called")
        mock_logger.debug.assert_any_call("Query params: {}")

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_anonymous_result(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {"query_string": b"access_token=invalid_token", "user": None}

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_auth.return_value = AnonymousUser()

            await middleware(scope, AsyncMock(), AsyncMock())

            self.assertIsInstance(scope["user"], AnonymousUser)
            mock_logger.debug.assert_any_call(
                "User authenticated: AnonymousUser"
            )

    def test_token_auth_middleware_stack(self):
        mock_inner = MagicMock()
        result = TokenAuthMiddlewareStack(mock_inner)

        self.assertIsInstance(result, TokenAuthMiddleware)
        self.assertEqual(result.inner, mock_inner)

    @patch("core.middleware.channels.logger")
    async def test_authenticate_token_expired_cleanup(self, mock_logger):
        user = await self.create_test_user()
        token_string = "test_key123456789abcdef"
        token_key = token_string[:15]
        expired_time = timezone.now() - timedelta(hours=1)
        token = await self.create_test_token(
            user, token_key=token_key, expiry=expired_time
        )

        result = await authenticate_token(token_string)

        self.assertIsInstance(result, AnonymousUser)
        mock_logger.debug.assert_any_call("Token expired and was cleaned up")

        with self.assertRaises(Token.DoesNotExist):
            await database_sync_to_async(Token.objects.get)(pk=token.pk)

    @patch("core.middleware.channels.logger")
    async def test_token_auth_middleware_logging_flow(self, mock_logger):
        async def mock_inner(scope, receive, send):
            pass

        middleware = TokenAuthMiddleware(mock_inner)

        scope = {
            "query_string": b"access_token=test_token&session_token=session_token&user_id=123",
            "user": None,
        }

        with patch("core.middleware.channels.authenticate_token") as mock_auth:
            mock_user = MagicMock()
            mock_user.is_anonymous = False
            mock_user.username = "testuser"
            mock_auth.return_value = mock_user

            await middleware(scope, AsyncMock(), AsyncMock())

            mock_logger.debug.assert_any_call("TokenAuthMiddleware called")
            mock_logger.debug.assert_any_call(
                "Query params: {'access_token': ['test_token'], 'session_token': ['session_token'], 'user_id': ['123']}"
            )
            mock_logger.debug.assert_any_call("Session token: session_to...")
            mock_logger.debug.assert_any_call("Access token: test_token...")
            mock_logger.debug.assert_any_call("User ID: 123")
            mock_logger.debug.assert_any_call(
                "TokenAuthMiddleware Token found, authenticating"
            )
            mock_logger.debug.assert_any_call("User authenticated: testuser")
