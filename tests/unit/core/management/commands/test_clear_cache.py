from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

from core.management.commands.clear_cache import Command


class TestClearCacheCommand:
    def test_command_help_text(self):
        command = Command()
        assert command.help == "Clears cache"

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_success(self, mock_cache):
        mock_cache.clear.return_value = None

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Successfully cleared cache" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_exception(self, mock_cache):
        mock_cache.clear.side_effect = Exception("Cache error")

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Error clearing cache: Cache error" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_via_call_command(self, mock_cache):
        mock_cache.clear.return_value = None

        out = StringIO()
        call_command("clear_cache", stdout=out)

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Successfully cleared cache" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_connection_error(self, mock_cache):
        mock_cache.clear.side_effect = ConnectionError(
            "Cannot connect to cache"
        )

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Error clearing cache: Cannot connect to cache" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_clear_cache_runtime_error(self, mock_cache):
        mock_cache.clear.side_effect = RuntimeError("Runtime error occurred")

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Error clearing cache: Runtime error occurred" in output

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_command_with_args_and_kwargs(self, mock_cache):
        mock_cache.clear.return_value = None

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle("arg1", "arg2", option1="value1", option2="value2")

        mock_cache.clear.assert_called_once()
        output = out.getvalue()
        assert "Successfully cleared cache" in output

    def test_command_instance_attributes(self):
        command = Command()

        assert hasattr(command, "help")
        assert hasattr(command, "handle")
        assert command.help == "Clears cache"

    @patch("core.management.commands.clear_cache.cache_instance")
    def test_multiple_command_executions(self, mock_cache):
        mock_cache.clear.return_value = None

        command = Command()

        for _ in range(3):
            out = StringIO()
            command.stdout = out
            command.handle()

            output = out.getvalue()
            assert "Successfully cleared cache" in output

        assert mock_cache.clear.call_count == 3
