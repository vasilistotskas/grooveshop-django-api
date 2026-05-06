from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from core.cache.service import PurgeReport, SurfaceResult
from core.management.commands.clear_cache import Command


class TestClearCacheCommand:
    def test_no_args_lists_surfaces(self):
        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(
            surfaces=[],
            all=False,
            dry_run=False,
            no_related=False,
            prefixes=None,
        )

        output = out.getvalue()
        assert "Available cache surfaces" in output
        assert "pay_way" in output
        # Heavy surface gets a marker.
        assert "[Heavy]" in output

    @patch("core.management.commands.clear_cache.CacheService")
    def test_purge_specific_surface(self, cache_service):
        cache_service.purge.return_value = PurgeReport(
            surfaces=[
                SurfaceResult(code="pay_way", django_deleted=4),
                SurfaceResult(code="orders", django_deleted=2),
            ]
        )

        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(
            surfaces=["pay_way"],
            all=False,
            dry_run=False,
            no_related=False,
            prefixes=None,
        )

        cache_service.purge.assert_called_once_with(
            ["pay_way"], dry_run=False, include_related=True
        )
        output = out.getvalue()
        assert "Purged 6 Django + 0 Nuxt keys" in output
        assert "pay_way" in output
        assert "orders" in output

    @patch("core.management.commands.clear_cache.CacheService")
    def test_dry_run_flag_passes_through(self, cache_service):
        cache_service.purge.return_value = PurgeReport(dry_run=True)

        command = Command()
        command.stdout = StringIO()
        command.stderr = StringIO()
        command.handle(
            surfaces=["pay_way"],
            all=False,
            dry_run=True,
            no_related=False,
            prefixes=None,
        )

        kwargs = cache_service.purge.call_args.kwargs
        assert kwargs["dry_run"] is True

    @patch("core.management.commands.clear_cache.CacheService")
    def test_all_flag_routes_through_purge_all(self, cache_service):
        cache_service.purge_all.return_value = PurgeReport()

        command = Command()
        command.stdout = StringIO()
        command.stderr = StringIO()
        command.handle(
            surfaces=[],
            all=True,
            dry_run=False,
            no_related=False,
            prefixes=None,
        )

        cache_service.purge_all.assert_called_once_with(dry_run=False)
        cache_service.purge.assert_not_called()

    @patch("core.management.commands.clear_cache.CacheService")
    def test_no_related_flag(self, cache_service):
        cache_service.purge.return_value = PurgeReport()

        command = Command()
        command.stdout = StringIO()
        command.stderr = StringIO()
        command.handle(
            surfaces=["pay_way"],
            all=False,
            dry_run=False,
            no_related=True,
            prefixes=None,
        )

        kwargs = cache_service.purge.call_args.kwargs
        assert kwargs["include_related"] is False

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_legacy_prefix_clear_still_works(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {"custom:": 3}

        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(
            surfaces=[],
            all=False,
            dry_run=False,
            no_related=False,
            prefixes=["custom:"],
        )

        mock_cache.clear_by_prefixes.assert_called_once_with(["custom:"])
        output = out.getvalue()
        assert "Cleared 3 keys" in output
        assert "Legacy mode" in output
