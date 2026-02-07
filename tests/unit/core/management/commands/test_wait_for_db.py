from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import OperationalError

import pytest

from core.management.commands.wait_for_db import Command


class TestWaitForDbCommand:
    def test_command_basic_instantiation(self):
        command = Command()
        assert hasattr(command, "handle")

    @patch.object(Command, "check")
    def test_database_available_immediately(self, mock_check):
        mock_check.return_value = None

        out = StringIO()
        call_command("wait_for_db", stdout=out)

        mock_check.assert_called_once_with(databases=["default"])
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_database_unavailable_then_available(self, mock_check, mock_sleep):
        mock_check.side_effect = [OperationalError("Connection failed"), None]

        out = StringIO()
        call_command("wait_for_db", "--timeout=0", stdout=out)

        assert mock_check.call_count == 2
        mock_sleep.assert_called_once_with(1)
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database unavailable" in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_database_multiple_retries(self, mock_check, mock_sleep):
        mock_check.side_effect = [
            OperationalError("Connection failed"),
            OperationalError("Still failing"),
            OperationalError("Another failure"),
            None,
        ]

        out = StringIO()
        call_command("wait_for_db", "--timeout=0", stdout=out)

        assert mock_check.call_count == 4
        assert mock_sleep.call_count == 3

        output = out.getvalue()
        assert "Waiting for database..." in output
        assert output.count("Database unavailable") == 3
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_command_via_call_command(self, mock_check, mock_sleep):
        mock_check.return_value = None

        out = StringIO()
        call_command("wait_for_db", stdout=out)

        mock_check.assert_called_once_with(databases=["default"])
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_timeout_raises_command_error(self, mock_check, mock_sleep):
        mock_check.side_effect = OperationalError("Connection failed")

        with patch("time.monotonic", side_effect=[0.0, 0.0, 31.0]):
            out = StringIO()
            with pytest.raises(
                CommandError, match="Database unavailable after 30 seconds"
            ):
                call_command("wait_for_db", "--timeout=30", stdout=out)

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_unlimited_timeout(self, mock_check, mock_sleep):
        mock_check.side_effect = [
            OperationalError("fail"),
            OperationalError("fail"),
            None,
        ]

        out = StringIO()
        call_command("wait_for_db", "--timeout=0", stdout=out)

        assert mock_check.call_count == 3
        output = out.getvalue()
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_custom_interval(self, mock_check, mock_sleep):
        mock_check.side_effect = [OperationalError("Connection failed"), None]

        out = StringIO()
        call_command("wait_for_db", "--timeout=0", "--interval=5", stdout=out)

        mock_sleep.assert_called_once_with(5)

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_check_called_with_default_database(self, mock_check, mock_sleep):
        mock_check.return_value = None

        command = Command()
        command.stdout = StringIO()

        command.handle(timeout=30, interval=1)

        mock_check.assert_called_once_with(databases=["default"])

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_success_message_style(self, mock_check, mock_sleep):
        mock_check.return_value = None

        command = Command()
        command.stdout = StringIO()

        with patch.object(command, "style") as mock_style:
            mock_style.SUCCESS.return_value = "STYLED: Database available!"

            command.handle(timeout=30, interval=1)

            mock_style.SUCCESS.assert_called_once_with("Database available!")
