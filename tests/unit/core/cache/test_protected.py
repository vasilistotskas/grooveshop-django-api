from __future__ import annotations

import pytest

from core.cache.protected import filter_protected, is_protected


class TestIsProtected:
    @pytest.mark.parametrize(
        "key",
        [
            "redis:1:throttle_user_42",
            "redis:1:throttle_anon_127.0.0.1",
            "session:abc",
            "knox_token:xyz",
            "bull:queue:42",
            "image:proxy:cache:item",
            "circuit_breaker:downstream",
            "dj_stripe:customer:123",
            "rate-limit:checkout:user_5",
        ],
    )
    def test_known_protected_keys_blocked(self, key):
        assert is_protected(key)

    @pytest.mark.parametrize(
        "key",
        [
            "redis:1:views.decorators.cache.cache_page.PayWayViewSet",
            "redis:1:parler.product.ProductTranslation.1.el",
            "cache:nitro:handlers:PayWayViewSet",
            "redis:1:meili:analytics:42",
        ],
    )
    def test_application_cache_keys_pass(self, key):
        assert not is_protected(key)


class TestFilterProtected:
    def test_returns_safe_and_blocked_split(self):
        keys = [
            "redis:1:views.decorators.cache.cache_page.X",
            "redis:1:throttle_user_5",
            "session:abc",
            "redis:1:parler.product.X.1.el",
        ]
        safe, blocked = filter_protected(keys)
        assert safe == [
            "redis:1:views.decorators.cache.cache_page.X",
            "redis:1:parler.product.X.1.el",
        ]
        assert blocked == [
            "redis:1:throttle_user_5",
            "session:abc",
        ]

    def test_empty_input_returns_empty_lists(self):
        safe, blocked = filter_protected([])
        assert safe == []
        assert blocked == []
