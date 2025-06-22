from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rest_framework.throttling import UserRateThrottle

from core.api.throttling import BurstRateThrottle

User = get_user_model()


class TestBurstRateThrottle:
    def setup_method(self):
        self.throttle = BurstRateThrottle()
        self.factory = RequestFactory()

    def test_throttle_scope_configuration(self):
        assert self.throttle.scope == "burst"

    def test_throttle_inherits_from_user_rate_throttle(self):
        assert isinstance(self.throttle, UserRateThrottle)

    def test_throttle_rate_configuration(self):
        request = self.factory.get("/")
        request.user = User()

        rate = self.throttle.get_rate()
        assert rate is None or isinstance(rate, str)

    def test_throttle_cache_key_generation(self):
        request = self.factory.get("/")
        request.user = User()
        request.user.pk = 123

        cache_key = self.throttle.get_cache_key(request, view=None)

        assert cache_key is not None
        assert "burst" in cache_key.lower() or "123" in str(cache_key)

    def test_throttle_anonymous_user(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()

        cache_key = self.throttle.get_cache_key(request, view=None)
        assert cache_key is not None
        assert "burst" in cache_key

    @patch.dict(
        settings.REST_FRAMEWORK, {"DEFAULT_THROTTLE_RATES": {"burst": "2/min"}}
    )
    def test_throttle_allow_request(self):
        request = self.factory.get("/")
        request.user = User()
        request.user.pk = 456

        with patch.object(self.throttle, "cache") as mock_cache:
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            result = self.throttle.allow_request(request, view=None)
            assert result is True

    def test_throttle_get_ident(self):
        request = self.factory.get("/")
        request.user = User()
        request.user.pk = 789

        ident = self.throttle.get_ident(request)

        assert ident is not None
        assert isinstance(ident, str)

    def test_throttle_scope_attribute_exists(self):
        assert hasattr(BurstRateThrottle, "scope")
        assert BurstRateThrottle.scope == "burst"

    def test_throttle_class_instantiation(self):
        throttle_instance = BurstRateThrottle()
        assert throttle_instance.scope == "burst"
        assert hasattr(throttle_instance, "allow_request")
        assert hasattr(throttle_instance, "get_cache_key")
        assert hasattr(throttle_instance, "get_rate")
