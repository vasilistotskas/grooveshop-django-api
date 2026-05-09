from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.cache.registry import CacheSurface, _reset_for_tests, register_surface
from core.cache.service import CacheService


@pytest.fixture
def isolated_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()
    from core.cache.surfaces import register_default_surfaces

    register_default_surfaces()


@pytest.fixture
def cache_instance_mock():
    with patch("core.cache.service.cache_instance") as mock:
        mock.keys.return_value = []
        mock.delete_raw_keys.return_value = 0
        yield mock


@pytest.fixture
def nuxt_client_mock():
    with patch("core.cache.service.nuxt_client") as mock:
        mock.request_purge.return_value = MagicMock(
            matched=0, deleted=0, blocked=0, error=None
        )
        yield mock


class TestPurge:
    def test_purge_runs_django_unlink_and_nuxt_request(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="pay_way",
                label="PayWay",
                description="",
                django_patterns=("*PayWayViewSet_*",),
                nuxt_patterns=("cache:nitro:handlers:PayWayViewSet*",),
            )
        )
        cache_instance_mock.keys.return_value = ["redis:1:k1", "redis:1:k2"]
        cache_instance_mock.delete_raw_keys.return_value = 2
        nuxt_client_mock.request_purge.return_value = MagicMock(
            matched=3, deleted=3, blocked=0, error=None
        )

        report = CacheService.purge(["pay_way"])

        cache_instance_mock.keys.assert_called_once_with("*PayWayViewSet_*")
        cache_instance_mock.delete_raw_keys.assert_called_once_with(
            ["redis:1:k1", "redis:1:k2"]
        )
        nuxt_client_mock.request_purge.assert_called_once_with(
            ["cache:nitro:handlers:PayWayViewSet*"], dry_run=False
        )
        assert report.total_django == 2
        assert report.total_nuxt == 3
        assert report.total_deleted == 5

    def test_dry_run_skips_unlink(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="pay_way",
                label="PayWay",
                description="",
                django_patterns=("*PayWayViewSet_*",),
            )
        )
        cache_instance_mock.keys.return_value = ["redis:1:k1"]

        CacheService.purge(["pay_way"], dry_run=True)

        cache_instance_mock.delete_raw_keys.assert_not_called()

    def test_protected_keys_filtered_out(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="x",
                label="X",
                description="",
                django_patterns=("*",),
            )
        )
        cache_instance_mock.keys.return_value = [
            "redis:1:views.decorators.cache.cache_page.OkViewSet",
            "redis:1:throttle_user_5",
        ]
        cache_instance_mock.delete_raw_keys.return_value = 1

        report = CacheService.purge(["x"])

        cache_instance_mock.delete_raw_keys.assert_called_once_with(
            ["redis:1:views.decorators.cache.cache_page.OkViewSet"]
        )
        assert report.surfaces[0].django_blocked == 1
        assert report.surfaces[0].django_matched == 2

    def test_unknown_surface_is_skipped(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        report = CacheService.purge(["does-not-exist"])

        assert report.surfaces == []
        cache_instance_mock.keys.assert_not_called()

    def test_django_redis_failure_isolated_to_one_surface(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="broken",
                label="Broken",
                description="",
                django_patterns=("*broken*",),
            )
        )
        register_surface(
            CacheSurface(
                code="ok",
                label="OK",
                description="",
                django_patterns=("*ok*",),
            )
        )
        cache_instance_mock.keys.side_effect = [
            ConnectionError("Redis down"),
            ["redis:1:ok-key"],
        ]
        cache_instance_mock.delete_raw_keys.return_value = 1

        report = CacheService.purge(["broken", "ok"], include_related=False)

        # First surface raised but report still has both entries.
        assert len(report.surfaces) == 2
        assert report.surfaces[0].django_error == "Redis down"
        assert report.surfaces[0].django_deleted == 0
        # Second surface succeeded.
        assert report.surfaces[1].django_error is None
        assert report.surfaces[1].django_deleted == 1

    def test_related_surfaces_expand_when_enabled(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="a",
                label="A",
                description="",
                django_patterns=("*A*",),
                related=("b",),
            )
        )
        register_surface(
            CacheSurface(
                code="b",
                label="B",
                description="",
                django_patterns=("*B*",),
            )
        )

        report = CacheService.purge(["a"], include_related=True)

        codes = [s.code for s in report.surfaces]
        assert codes == ["a", "b"]

    def test_related_skipped_when_disabled(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="a",
                label="A",
                description="",
                related=("b",),
            )
        )
        register_surface(CacheSurface(code="b", label="B", description=""))

        report = CacheService.purge(["a"], include_related=False)

        codes = [s.code for s in report.surfaces]
        assert codes == ["a"]


class TestPurgeAll:
    def test_skips_danger_surfaces(
        self, isolated_registry, cache_instance_mock, nuxt_client_mock
    ):
        register_surface(
            CacheSurface(
                code="safe",
                label="Safe",
                description="",
                django_patterns=("*safe*",),
            )
        )
        register_surface(
            CacheSurface(
                code="risky",
                label="Risky",
                description="",
                django_patterns=("*risky*",),
                danger=True,
            )
        )

        report = CacheService.purge_all()

        codes = [s.code for s in report.surfaces]
        assert "safe" in codes
        assert "risky" not in codes


class TestCount:
    def test_counts_keys_per_surface(
        self, isolated_registry, cache_instance_mock
    ):
        register_surface(
            CacheSurface(
                code="x",
                label="X",
                description="",
                django_patterns=("*foo*", "*bar*"),
            )
        )
        cache_instance_mock.keys.side_effect = [
            ["a", "b"],
            ["c"],
        ]

        result = CacheService.count(["x"])

        assert result == {"x": 3}
