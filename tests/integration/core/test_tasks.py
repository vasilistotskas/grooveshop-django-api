import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError
from django.test import override_settings
from django.utils import timezone

from cart.models import Cart
from cart.tasks import cleanup_abandoned_carts
from core.celery import celery_app
from core.tasks import (
    MonitoredTask,
    backup_database_task,
    cleanup_old_backups,
    clear_all_cache_task,
    clear_development_log_files_task,
    clear_duplicate_history_task,
    clear_expired_notifications_task,
    clear_expired_sessions_task,
    clear_old_history_task,
    validate_task_configuration,
)
from emails.models import EmailProvider
from emails.transport import Receipt
from tests.utils.emails import open_email_gates  # noqa: F401

#: What the transport answers for a message it sent (a mocked ``deliver``).
SMTP_RECEIPT = Receipt(provider=EmailProvider.SMTP)


# Registered on the app, so it has the request stack Celery gives every
# real task (a bare ``MonitoredTask()`` has none, and ``on_success`` reads
# the request for its database cost).
@celery_app.task(base=MonitoredTask, name="test_task")
def _monitored_task():
    return None


class TestMonitoredTask:
    @patch("core.monitored_task.logger")
    def test_on_success_logs_completion(self, mock_logger):
        task = _monitored_task

        task.on_success(retval="success", task_id="12345", args=(), kwargs={})

        mock_logger.info.assert_called_once_with(
            "Task test_task completed successfully. Task ID: 12345",
            extra={"fields": {}},
        )

    @patch("core.monitored_task.logger")
    def test_on_failure_logs_error(self, mock_logger):
        task = _monitored_task

        exception = Exception("Test error")
        task.on_failure(
            exc=exception, task_id="12345", args=(), kwargs={}, einfo=None
        )

        # MonitoredTask.on_failure logs args/kwargs for debuggability; mask
        # is applied per-key by _safe_repr.
        mock_logger.error.assert_called_once_with(
            "Task %s failed. Task ID: %s, Error: %s, args=%s, kwargs=%s",
            "test_task",
            "12345",
            exception,
            [],
            {},
            exc_info=exception,
            extra={"fields": {}},
        )


@pytest.mark.django_db
class TestClearExpiredSessionsTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_successful_session_cleanup(self, mock_logger, mock_call_command):
        result = clear_expired_sessions_task()

        mock_call_command.assert_called_once_with("clearsessions", verbosity=0)
        assert result["status"] == "success"
        assert result["message"] == "All expired sessions deleted"
        mock_logger.info.assert_any_call("Starting expired sessions cleanup")

    @patch("core.tasks.management.call_command")
    def test_command_error_fails_the_task(self, mock_call_command):
        mock_call_command.side_effect = CommandError("Command failed")

        with pytest.raises(CommandError, match="Command failed"):
            clear_expired_sessions_task()

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_unexpected_error_handling(self, mock_logger, mock_call_command):
        mock_call_command.side_effect = RuntimeError("Unexpected error")

        with pytest.raises(RuntimeError):
            clear_expired_sessions_task()

        mock_logger.exception.assert_called_with(
            "Unexpected error in clear_expired_sessions"
        )


class TestClearAllCacheTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_successful_cache_cleanup(self, mock_logger, mock_call_command):
        """``--all`` is load-bearing, not cosmetic.

        ``clear_cache`` with no surfaces and no ``--all`` takes its
        "list available surfaces" branch and purges NOTHING, so the
        monthly beat task was a silent no-op that still reported
        success. This assertion is what keeps the flag from being
        dropped again.
        """
        result = clear_all_cache_task()

        mock_call_command.assert_called_once_with(
            "clear_cache", "--all", verbosity=0
        )
        assert result["status"] == "success"
        assert result["message"] == "Cache surfaces purged"

    @patch("core.tasks.management.call_command")
    def test_cache_cleanup_command_error_fails_the_task(
        self, mock_call_command
    ):
        mock_call_command.side_effect = CommandError("Cache error")

        with pytest.raises(CommandError, match="Cache error"):
            clear_all_cache_task()


@pytest.mark.django_db
class TestClearDuplicateHistoryTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_duplicate_history_cleanup_with_defaults(
        self, mock_logger, mock_call_command
    ):
        result = clear_duplicate_history_task()

        # `--auto` is not optional. Without a model name or this flag,
        # simple-history's command prints "Please specify a model or use
        # the --auto option" and issues ZERO queries, while the task
        # goes on to report success. This assertion used to pin the
        # argv WITHOUT it, locking the no-op in; the sibling
        # `clear_old_history_task` test has always asserted `--auto`.
        mock_call_command.assert_called_once_with(
            "clean_duplicate_history", "--auto"
        )
        assert result["status"] == "success"
        assert "Duplicate history entries cleaned" in result["message"]

    @patch("django.core.management.call_command")
    def test_duplicate_history_cleanup_with_parameters(self, mock_call_command):
        excluded_fields = ["field1", "field2"]
        minutes = 30

        result = clear_duplicate_history_task(
            excluded_fields=excluded_fields, minutes=minutes
        )

        mock_call_command.assert_called_once_with(
            "clean_duplicate_history",
            "--auto",
            "-m",
            "30",
            "--excluded_fields",
            "field1",
            "field2",
        )
        assert result["status"] == "success"
        assert result["parameters"]["excluded_fields"] == excluded_fields
        assert result["parameters"]["minutes"] == minutes

    @patch("core.tasks.management.call_command")
    def test_duplicate_history_cleanup_command_error_fails_the_task(
        self, mock_call_command
    ):
        mock_call_command.side_effect = CommandError("History error")

        with pytest.raises(CommandError, match="History error"):
            clear_duplicate_history_task()


@pytest.mark.django_db
class TestClearOldHistoryTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_old_history_cleanup_with_default_days(
        self, mock_logger, mock_call_command
    ):
        result = clear_old_history_task()

        mock_call_command.assert_called_once_with(
            "clean_old_history", "--days", "365", "--auto"
        )
        assert result["status"] == "success"
        assert (
            "Old history entries cleaned (older than 365 days)"
            in result["message"]
        )

    @patch("core.tasks.management.call_command")
    def test_old_history_cleanup_with_custom_days(self, mock_call_command):
        result = clear_old_history_task(days=30)

        mock_call_command.assert_called_once_with(
            "clean_old_history", "--days", "30", "--auto"
        )
        assert result["status"] == "success"


@pytest.mark.django_db
class TestClearExpiredNotificationsTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_expired_notifications_cleanup(
        self, mock_logger, mock_call_command
    ):
        result = clear_expired_notifications_task(days=30)

        mock_call_command.assert_called_once_with(
            "expire_notifications", "--days", "30"
        )
        assert result["status"] == "success"
        assert result["message"] == "Expired notifications deleted"

    @patch("core.tasks.management.call_command")
    def test_expired_notifications_with_default_days(self, mock_call_command):
        result = clear_expired_notifications_task()

        mock_call_command.assert_called_once_with(
            "expire_notifications", "--days", "365"
        )
        assert result["status"] == "success"


@pytest.mark.django_db
class TestACommandFailureIsATaskFailure:
    """None of the cleanup tasks may report success on a failed command.

    Each caught `CommandError`, logged it and returned
    `{"status": "error"}`. Celery therefore recorded SUCCESS,
    `MonitoredTask.on_success` logged "completed successfully", and the
    `autoretry_for=(Exception,)` on every one of them could never fire —
    for the single most likely failure a management-command wrapper has.
    That is the same defect `backup_database_task` carried until a
    pg_dump/server major mismatch went unnoticed for a week while the
    backup volume drained (2026-09-02); the fix there is the precedent
    this follows, and `sync_meilisearch_indexes` already re-raised.

    Parametrised over the whole family so a sixth wrapper cannot be
    added with the old shape.
    """

    @pytest.mark.parametrize(
        "task",
        [
            clear_expired_sessions_task,
            clear_all_cache_task,
            clear_duplicate_history_task,
            clear_old_history_task,
            clear_expired_notifications_task,
        ],
        ids=lambda t: t.__name__,
    )
    @patch("core.tasks.management.call_command")
    def test_command_error_propagates(self, mock_call_command, task):
        mock_call_command.side_effect = CommandError("the command failed")

        with pytest.raises(CommandError, match="the command failed"):
            task()


class TestClearLogFilesTask:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.temp_dir = tempfile.mkdtemp()
        self.logs_path = os.path.join(self.temp_dir, "logs")
        os.makedirs(self.logs_path)
        yield
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.getenv")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.os.listdir")
    @patch("core.tasks.os.path.isfile")
    @patch("core.tasks.os.path.getmtime")
    @patch("core.tasks.os.path.getsize")
    @patch("core.tasks.os.remove")
    @patch("core.tasks.logger")
    def test_clear_old_log_files(
        self,
        mock_logger,
        mock_remove,
        mock_getsize,
        mock_getmtime,
        mock_isfile,
        mock_listdir,
        mock_join,
        mock_exists,
        mock_getenv,
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "KUBERNETES_SERVICE_HOST": None,
        }.get(key, default)

        def mock_exists_side_effect(path):
            return bool(path == "/.dockerenv" or path == self.logs_path)

        mock_exists.side_effect = mock_exists_side_effect
        mock_join.return_value = self.logs_path
        mock_listdir.return_value = [
            "old_log.log.1",
            "recent_log.log.2",
            "current_log.log",
            "not_a_file",
        ]

        old_time = (timezone.now() - timedelta(days=40)).timestamp()
        recent_time = (timezone.now() - timedelta(days=10)).timestamp()

        def mock_isfile_side_effect(path):
            return "not_a_file" not in path

        def mock_getmtime_side_effect(path):
            if "old_log.log.1" in path:
                return old_time
            return recent_time

        def mock_getsize_side_effect(path):
            return 1024 if "old_log.log.1" in path else 512

        mock_isfile.side_effect = mock_isfile_side_effect
        mock_getmtime.side_effect = mock_getmtime_side_effect
        mock_getsize.side_effect = mock_getsize_side_effect

        result = clear_development_log_files_task(days=30)

        assert result["status"] == "success"
        assert "deleted_count" in result
        mock_getmtime.assert_called()

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.getenv")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.logger")
    def test_clear_log_files_no_logs_directory(
        self, mock_logger, mock_join, mock_exists, mock_getenv
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "KUBERNETES_SERVICE_HOST": None,
        }.get(key, default)

        def mock_exists_side_effect(path):
            if path == "/.dockerenv":
                return True
            elif path == "/nonexistent/logs":
                return False
            return False

        mock_exists.side_effect = mock_exists_side_effect
        mock_join.return_value = "/nonexistent/logs"

        result = clear_development_log_files_task()

        assert result["status"] == "skipped"
        assert result["reason"] == "logs directory not found"
        mock_logger.warning.assert_called()

    @patch("core.tasks.os.getenv")
    @patch("core.tasks.os.path.exists")
    def test_clear_log_files_kubernetes_environment(
        self, mock_exists, mock_getenv
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "KUBERNETES_SERVICE_HOST": "10.0.0.1",
        }.get(key, default)

        result = clear_development_log_files_task()

        assert result["status"] == "skipped"
        assert result["reason"] == "kubernetes environment detected"

    @patch("core.tasks.os.getenv")
    @patch("core.tasks.os.path.exists")
    def test_clear_log_files_non_docker_environment(
        self, mock_exists, mock_getenv
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "KUBERNETES_SERVICE_HOST": None,
        }.get(key, default)

        def mock_exists_side_effect(path):
            if path == "/.dockerenv":
                return False
            return False

        mock_exists.side_effect = mock_exists_side_effect

        result = clear_development_log_files_task()

        assert result["status"] == "skipped"
        assert result["reason"] == "not in docker environment"

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.getenv")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.os.listdir")
    @patch("core.tasks.logger")
    def test_clear_log_files_os_error(
        self, mock_logger, mock_listdir, mock_join, mock_exists, mock_getenv
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "KUBERNETES_SERVICE_HOST": None,
        }.get(key, default)

        def mock_exists_side_effect(path):
            return bool(path == "/.dockerenv" or path == self.logs_path)

        mock_exists.side_effect = mock_exists_side_effect
        mock_join.return_value = self.logs_path
        mock_listdir.side_effect = OSError("Permission denied")

        with pytest.raises(OSError):
            clear_development_log_files_task()

        mock_logger.exception.assert_called()


class TestBackupDatabaseTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_backup_database_success(
        self, mock_logger, mock_path, mock_call_command
    ):
        mock_backup_dir = Mock()
        mock_backup_file = Mock()
        mock_backup_file.name = "backup_20231201_120000.sql"
        mock_backup_file.stat.return_value.st_size = 1024000
        mock_backup_file.stat.return_value.st_ctime = timezone.now().timestamp()

        mock_backup_dir.exists.return_value = True
        mock_backup_dir.iterdir.return_value = [mock_backup_file]
        mock_path.return_value.__truediv__.return_value = mock_backup_dir

        result = backup_database_task(
            output_dir="backups",
            filename="test_backup",
            compress=True,
            format_type="custom",
        )

        assert result["status"] == "success"
        assert result["file_size"] == 1024000
        assert (
            "Database backup completed successfully" in result["result_message"]
        )

        mock_call_command.assert_called_once_with(
            "backup_database",
            "--output-dir",
            "backups",
            "--filename",
            "test_backup",
            "--compress",
            "--format",
            "custom",
        )

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_backup_database_command_error(
        self, mock_logger, mock_call_command
    ):
        mock_call_command.side_effect = CommandError("Backup failed")

        # Must propagate: a swallowed CommandError reported SUCCESS to
        # Celery and let the chained cleanup prune old backups after a
        # failed dump.
        with pytest.raises(CommandError, match="Backup failed"):
            backup_database_task()

        mock_logger.exception.assert_called_once()

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_backup_database_no_backup_dir(
        self, mock_logger, mock_path, mock_call_command
    ):
        mock_backup_dir = Mock()
        mock_backup_dir.exists.return_value = False
        mock_path.return_value.__truediv__.return_value = mock_backup_dir

        result = backup_database_task()

        assert result["status"] == "success"
        assert result["file_size"] == 0
        assert result["backup_file"] is None

    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_cleanup_old_backups_success(self, mock_logger, mock_path):
        mock_backup_dir = Mock()
        mock_backup_dir.exists.return_value = True

        old_file = Mock()
        old_file.is_file.return_value = True
        old_file.name = "old_backup.sql"
        old_file.stat.return_value.st_mtime = (
            timezone.now() - timedelta(days=40)
        ).timestamp()
        old_file.stat.return_value.st_size = 1024

        recent_file = Mock()
        recent_file.is_file.return_value = True
        recent_file.name = "recent_backup.sql"
        recent_file.stat.return_value.st_mtime = (
            timezone.now() - timedelta(days=10)
        ).timestamp()

        mock_backup_dir.iterdir.return_value = [old_file, recent_file]

        # Mock Path() to handle is_absolute() and division operator
        mock_path_instance = Mock()
        mock_path_instance.is_absolute.return_value = False
        mock_path_instance.__truediv__ = Mock(return_value=mock_backup_dir)
        mock_path.return_value = mock_path_instance

        result = cleanup_old_backups(days=30, backup_dir="backups")

        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert result["total_size_freed"] == 1024
        old_file.unlink.assert_called_once()
        recent_file.unlink.assert_not_called()

    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_cleanup_old_backups_no_directory(self, mock_logger, mock_path):
        mock_backup_dir = Mock()
        mock_backup_dir.exists.return_value = False

        # Mock Path() to handle is_absolute() and division operator
        mock_path_instance = Mock()
        mock_path_instance.is_absolute.return_value = False
        mock_path_instance.__truediv__ = Mock(return_value=mock_backup_dir)
        mock_path.return_value = mock_path_instance

        result = cleanup_old_backups()

        assert result["status"] == "skipped"
        assert result["reason"] == "backup directory not found"
        mock_logger.warning.assert_called()

    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_cleanup_old_backups_file_error(self, mock_logger, mock_path):
        mock_backup_dir = Mock()
        mock_backup_dir.exists.return_value = True

        error_file = Mock()
        error_file.is_file.return_value = True
        error_file.name = "error_backup.sql"
        error_file.stat.side_effect = OSError("Permission denied")

        mock_backup_dir.iterdir.return_value = [error_file]

        # Mock Path() to handle is_absolute() and division operator
        mock_path_instance = Mock()
        mock_path_instance.is_absolute.return_value = False
        mock_path_instance.__truediv__ = Mock(return_value=mock_backup_dir)
        mock_path.return_value = mock_path_instance

        result = cleanup_old_backups()

        assert result["status"] == "success"
        assert result["deleted_count"] == 0
        mock_logger.error.assert_called()


class TestValidateTaskConfiguration:
    @override_settings(
        DEFAULT_FROM_EMAIL="test@example.com",
        ADMINS=["admin@example.com"],
        MEDIA_ROOT="/tmp/media",
        BASE_DIR="/tmp/base",
    )
    @patch("core.tasks.logger")
    def test_validate_task_configuration_success(self, mock_logger):
        validate_task_configuration()

        mock_logger.info.assert_called_with(
            "Task configuration validated successfully"
        )

    @override_settings()
    def test_validate_task_configuration_missing_settings(self):
        if hasattr(settings, "DEFAULT_FROM_EMAIL"):
            del settings.DEFAULT_FROM_EMAIL
        if hasattr(settings, "ADMINS"):
            del settings.ADMINS

        with pytest.raises(
            ImproperlyConfigured, match="Missing required settings"
        ):
            validate_task_configuration()


@pytest.mark.django_db
class TestTaskIntegration:
    @patch("core.tasks.management.call_command")
    def test_multiple_cleanup_tasks_workflow(self, mock_call_command):
        cart = Cart.objects.create(user=None)

        session_result = clear_expired_sessions_task()
        cache_result = clear_all_cache_task()

        Cart.objects.filter(id=cart.id).update(
            last_activity=timezone.now() - timedelta(days=10)
        )
        cart_result = cleanup_abandoned_carts()

        assert session_result["status"] == "success"
        assert cache_result["status"] == "success"
        assert cart_result["status"] == "success"
        assert cart_result["deleted_count"] == 1

        assert not Cart.objects.filter(user=None).exists()

        expected_calls = ["clearsessions", "clear_cache"]
        actual_calls = [call[0][0] for call in mock_call_command.call_args_list]
        for expected_call in expected_calls:
            assert expected_call in actual_calls
