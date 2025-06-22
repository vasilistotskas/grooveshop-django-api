from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db.utils import OperationalError

from core.management.commands.wait_for_db import Command


class TestWaitForDbCommand:
    def test_command_basic_instantiation(self):
        command = Command()
        assert hasattr(command, "handle")

    @patch.object(Command, "check")
    def test_database_available_immediately(self, mock_check):
        mock_check.return_value = None

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        mock_check.assert_called_once_with(databases=["default"])
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_database_unavailable_then_available(self, mock_check, mock_sleep):
        mock_check.side_effect = [OperationalError("Connection failed"), None]

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        assert mock_check.call_count == 2
        mock_sleep.assert_called_once_with(1)
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database unavailable, waiting 1 second..." in output
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
        command = Command()
        command.stdout = out

        command.handle()

        assert mock_check.call_count == 4
        assert mock_sleep.call_count == 3

        output = out.getvalue()
        assert "Waiting for database..." in output
        assert output.count("Database unavailable, waiting 1 second...") == 3
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
    def test_command_with_args_and_options(self, mock_check, mock_sleep):
        mock_check.return_value = None

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle("arg1", "arg2", option1="value1", option2="value2")

        mock_check.assert_called_once_with(databases=["default"])
        output = out.getvalue()
        assert "Waiting for database..." in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_operational_error_with_message(self, mock_check, mock_sleep):
        error_message = "FATAL: database 'test_db' does not exist"
        mock_check.side_effect = [OperationalError(error_message), None]

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        assert mock_check.call_count == 2
        mock_sleep.assert_called_once_with(1)
        output = out.getvalue()
        assert "Database unavailable, waiting 1 second..." in output
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_sleep_duration_is_one_second(self, mock_check, mock_sleep):
        mock_check.side_effect = [OperationalError("Connection failed"), None]

        command = Command()
        command.stdout = StringIO()

        command.handle()

        mock_sleep.assert_called_once_with(1)

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_check_called_with_default_database(self, mock_check, mock_sleep):
        mock_check.return_value = None

        command = Command()
        command.stdout = StringIO()

        command.handle()

        mock_check.assert_called_once_with(databases=["default"])

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_database_check_loop_behavior(self, mock_check, mock_sleep):
        mock_check.side_effect = [
            OperationalError("First failure"),
            OperationalError("Second failure"),
            None,
        ]

        out = StringIO()
        command = Command()
        command.stdout = out

        command.handle()

        assert mock_check.call_count == 3
        assert mock_sleep.call_count == 2

        output = out.getvalue()
        assert "Waiting for database..." in output
        assert output.count("Database unavailable, waiting 1 second...") == 2
        assert "Database available!" in output

    @patch("time.sleep")
    @patch.object(Command, "check")
    def test_success_message_style(self, mock_check, mock_sleep):
        mock_check.return_value = None

        command = Command()
        command.stdout = StringIO()

        with patch.object(command, "style") as mock_style:
            mock_style.SUCCESS.return_value = "STYLED: Database available!"

            command.handle()

            mock_style.SUCCESS.assert_called_once_with("Database available!")
