from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from allauth.headless.tokens.strategies.sessions import (
    SessionTokenStrategy as BaseSessionTokenStrategy,
)
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from knox.models import get_token_model

from core.api.tokens import (
    KNOX_ABSOLUTE_MAX_AGE,
    AuthToken,
    BoundedTokenAuthentication,
    SessionTokenStrategy,
)

User = get_user_model()


pytestmark = pytest.mark.assert_english


class TestSessionTokenStrategy:
    @pytest.fixture(autouse=True)
    def _disable_knox_token_limit(self, monkeypatch):
        """Bypass the TOKEN_LIMIT_PER_USER pruning path so unit tests stay
        DB-free.  The limit branch in ``create_access_token`` issues
        ``AuthToken.objects.filter(...).count()`` which would otherwise
        require a DB-backed test (the project sets the limit to 10 in
        ``settings.REST_KNOX``).  The pruning logic itself has no specific
        coverage here — these tests focus on the create-and-return path.
        """
        from core.api.tokens import knox_settings

        monkeypatch.setattr(knox_settings, "TOKEN_LIMIT_PER_USER", None)

    def setup_method(self):
        self.strategy = SessionTokenStrategy()
        self.factory = RequestFactory()

    def test_strategy_inherits_from_base_session_token_strategy(self):
        assert isinstance(self.strategy, BaseSessionTokenStrategy)

    @patch("core.api.tokens.AuthToken.objects.create")
    def test_create_access_token_success(self, mock_create):
        user = User(id=1, email="test@example.com")
        request = self.factory.get("/")
        request.user = user

        mock_token_instance = MagicMock()
        mock_token_value = "test_token_value_123"
        mock_create.return_value = (mock_token_instance, mock_token_value)

        token = self.strategy.create_access_token(request)

        assert token == mock_token_value
        mock_create.assert_called_once_with(user)

    @patch("core.api.tokens.AuthToken.objects.create")
    def test_create_access_token_with_different_users(self, mock_create):
        user1 = User(id=1, email="user1@example.com")
        request1 = self.factory.get("/")
        request1.user = user1

        mock_create.return_value = (MagicMock(), "token1")
        token1 = self.strategy.create_access_token(request1)
        assert token1 == "token1"

        user2 = User(id=2, email="user2@example.com")
        request2 = self.factory.get("/")
        request2.user = user2

        mock_create.return_value = (MagicMock(), "token2")
        token2 = self.strategy.create_access_token(request2)
        assert token2 == "token2"

        assert mock_create.call_count == 2

    @patch("core.api.tokens.AuthToken.objects.create")
    def test_create_access_token_exception_handling(self, mock_create):
        user = User(id=1, email="testuser@example.com")
        request = self.factory.get("/")
        request.user = user

        mock_create.side_effect = Exception("Token creation failed")

        with pytest.raises(Exception, match="Token creation failed"):
            self.strategy.create_access_token(request)

    def test_auth_token_model_import(self):
        expected_model = get_token_model()
        assert AuthToken == expected_model

    def test_strategy_has_required_methods(self):
        assert hasattr(self.strategy, "create_access_token")
        assert callable(self.strategy.create_access_token)

    @patch("core.api.tokens.AuthToken.objects.create")
    def test_create_access_token_return_type(self, mock_create):
        user = User(id=1, email="testuser@example.com")
        request = self.factory.get("/")
        request.user = user

        mock_instance = MagicMock()
        mock_token = "expected_token_string"
        mock_create.return_value = (mock_instance, mock_token)

        result = self.strategy.create_access_token(request)

        assert result == mock_token
        assert isinstance(result, str)

    def test_strategy_class_instantiation(self):
        strategy = SessionTokenStrategy()
        assert strategy is not None
        assert hasattr(strategy, "create_access_token")


class TestAuthTokenModel:
    def test_auth_token_model_is_imported(self):
        assert AuthToken is not None
        assert hasattr(AuthToken, "objects")

    def test_auth_token_get_token_model(self):
        token_model = get_token_model()
        assert AuthToken == token_model

    def test_auth_token_has_manager(self):
        assert hasattr(AuthToken, "objects")
        assert hasattr(AuthToken.objects, "create")


@pytest.mark.django_db
class TestBoundedTokenAuthentication:
    """Tests for the absolute max-age cap on Knox tokens.

    BoundedTokenAuthentication rejects tokens whose *creation* timestamp
    is older than KNOX_ABSOLUTE_MAX_AGE (30 days), regardless of their
    expiry field. This closes the gap where AUTO_REFRESH could push expiry
    forward but the session would be active for arbitrarily long.
    """

    def test_fresh_token_is_accepted(self):
        """A newly-created token passes the absolute-age check."""

        user = User.objects.create_user(
            username="freshuser",
            email="freshuser@example.com",
            password="testpass",
        )
        _, raw_token = AuthToken.objects.create(user)

        auth = BoundedTokenAuthentication()
        # encode as bytes to match how authenticate_credentials receives it
        result_user, result_token = auth.authenticate_credentials(
            raw_token.encode()
        )
        assert result_user == user

    def test_token_older_than_absolute_max_age_is_rejected(self):
        """A token whose created timestamp is older than KNOX_ABSOLUTE_MAX_AGE
        must be rejected with 401 and deleted from the database."""
        from rest_framework.exceptions import AuthenticationFailed

        user = User.objects.create_user(
            username="oldtokenuser",
            email="oldtokenuser@example.com",
            password="testpass",
        )
        _, raw_token = AuthToken.objects.create(user)

        # Back-date the token's created timestamp to exceed the max age.
        old_created = timezone.now() - KNOX_ABSOLUTE_MAX_AGE - timedelta(days=1)
        AuthToken.objects.filter(user=user).update(created=old_created)

        auth = BoundedTokenAuthentication()
        with pytest.raises(AuthenticationFailed) as exc_info:
            auth.authenticate_credentials(raw_token.encode())

        assert "maximum lifetime" in str(exc_info.value).lower()

        # The expired token must be deleted so it cannot accumulate.
        assert not AuthToken.objects.filter(user=user).exists()

    def test_token_exactly_at_max_age_boundary_is_rejected(self):
        """A token created exactly KNOX_ABSOLUTE_MAX_AGE ago is rejected
        (boundary is exclusive — age must be strictly less than max)."""
        from rest_framework.exceptions import AuthenticationFailed

        user = User.objects.create_user(
            username="boundaryuser",
            email="boundaryuser@example.com",
            password="testpass",
        )
        _, raw_token = AuthToken.objects.create(user)

        # created = now - max_age → age == max_age → over the limit
        exactly_at_limit = timezone.now() - KNOX_ABSOLUTE_MAX_AGE
        AuthToken.objects.filter(user=user).update(created=exactly_at_limit)

        auth = BoundedTokenAuthentication()
        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(raw_token.encode())
