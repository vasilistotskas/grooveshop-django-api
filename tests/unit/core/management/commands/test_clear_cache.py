from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings

from core.management.commands.clear_cache import Command


class TestClearCacheCommand:
    def test_command_help_text(self):
        command = Command()
        assert (
            command.help
            == "Clear cached keys by prefix (default: Django + Nuxt cache)"
        )

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_default_prefixes(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {
            "redis:1:": 10,
            "cache:": 5,
        }

        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(prefixes=None)

        mock_cache.clear_by_prefixes.assert_called_once()
        output = out.getvalue()
        assert "Successfully cleared 15 cache keys" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_custom_prefixes(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {"custom:": 3}

        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(prefixes=["custom:"])

        mock_cache.clear_by_prefixes.assert_called_once_with(["custom:"])
        output = out.getvalue()
        assert "Successfully cleared 3 cache keys" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_exception(self, mock_cache):
        mock_cache.clear_by_prefixes.side_effect = Exception("Redis error")

        out = StringIO()
        err = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = err

        command.handle(prefixes=["redis:1:"])

        output = err.getvalue()
        assert "Error clearing cache: Redis error" in output

    @override_settings(CACHE_CLEAR_PREFIXES=[])
    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_no_prefixes_configured(self, mock_cache):
        err = StringIO()
        command = Command()
        command.stdout = StringIO()
        command.stderr = err

        command.handle(prefixes=None)

        mock_cache.clear_by_prefixes.assert_not_called()
        output = err.getvalue()
        assert "No prefixes configured" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_via_call_command(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {"redis:1:": 5}

        out = StringIO()
        call_command("clear_cache", stdout=out)

        mock_cache.clear_by_prefixes.assert_called_once()

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_with_cli_prefixes(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {"redis:1:": 5}

        out = StringIO()
        call_command("clear_cache", "--prefixes", "redis:1:", stdout=out)

        mock_cache.clear_by_prefixes.assert_called_once_with(["redis:1:"])

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_per_prefix_output(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {
            "redis:1:": 10,
            "cache:": 5,
        }

        out = StringIO()
        command = Command()
        command.stdout = out
        command.stderr = StringIO()

        command.handle(prefixes=["redis:1:", "cache:"])

        output = out.getvalue()
        assert "redis:1:* -> 10 keys deleted" in output
        assert "cache:* -> 5 keys deleted" in output

    def test_command_instance_attributes(self):
        command = Command()

        assert hasattr(command, "help")
        assert hasattr(command, "handle")
        assert hasattr(command, "add_arguments")

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_connection_error(self, mock_cache):
        mock_cache.clear_by_prefixes.side_effect = ConnectionError(
            "Cannot connect to cache"
        )

        err = StringIO()
        command = Command()
        command.stdout = StringIO()
        command.stderr = err

        command.handle(prefixes=["redis:1:"])

        mock_cache.clear_by_prefixes.assert_called_once()
        output = err.getvalue()
        assert "Error clearing cache: Cannot connect to cache" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_multiple_command_executions(self, mock_cache):
        mock_cache.clear_by_prefixes.return_value = {"redis:1:": 1}

        command = Command()

        for _ in range(3):
            out = StringIO()
            command.stdout = out
            command.stderr = StringIO()
            command.handle(prefixes=["redis:1:"])

            output = out.getvalue()
            assert "Successfully cleared" in output

        assert mock_cache.clear_by_prefixes.call_count == 3
