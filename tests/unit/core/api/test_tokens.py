from unittest.mock import MagicMock, patch

import pytest
from allauth.headless.tokens.sessions import (
    SessionTokenStrategy as BaseSessionTokenStrategy,
)
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from knox.models import get_token_model

from core.api.tokens import AuthToken, SessionTokenStrategy

User = get_user_model()


class TestSessionTokenStrategy:
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
