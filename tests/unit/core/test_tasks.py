import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from cart.models import Cart
from core.tasks import (
    MonitoredTask,
    backup_database_task,
    cleanup_old_backups,
    clear_all_cache_task,
    clear_carts_for_none_users_task,
    clear_duplicate_history_task,
    clear_expired_notifications_task,
    clear_expired_sessions_task,
    clear_log_files_task,
    clear_old_history_task,
    monitor_system_health,
    scheduled_database_backup,
    send_inactive_user_notifications,
    track_task_metrics,
    validate_task_configuration,
)
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestMonitoredTask(TestCase):
    def setUp(self):
        self.task = MonitoredTask()
        self.task.name = "test_task"

    @patch("core.tasks.logger")
    def test_on_success_logs_completion(self, mock_logger):
        self.task.on_success(
            retval="success", task_id="12345", args=(), kwargs={}
        )

        mock_logger.info.assert_called_once_with(
            "Task test_task completed successfully. Task ID: 12345"
        )

    @patch("core.tasks.logger")
    def test_on_failure_logs_error(self, mock_logger):
        exception = Exception("Test error")
        self.task.on_failure(
            exc=exception, task_id="12345", args=(), kwargs={}, einfo=None
        )

        mock_logger.error.assert_called_once_with(
            "Task test_task failed. Task ID: 12345, Error: Test error"
        )


class TestTrackTaskMetrics(TestCase):
    @patch("core.tasks.logger")
    @patch("time.time")
    def test_successful_task_tracking(self, mock_time, mock_logger):
        mock_time.side_effect = [1000.0, 1001.5]

        @track_task_metrics
        def dummy_task():
            return "success"

        result = dummy_task()

        self.assertEqual(result, "success")
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        self.assertIn("Task dummy_task completed successfully", call_args[0][0])
        self.assertEqual(call_args[1]["extra"]["duration"], 1.5)
        self.assertEqual(call_args[1]["extra"]["status"], "success")

    @patch("core.tasks.logger")
    @patch("time.time")
    def test_failed_task_tracking(self, mock_time, mock_logger):
        mock_time.side_effect = [1000.0, 1001.5]

        @track_task_metrics
        def failing_task():
            raise ValueError("Test error")

        with self.assertRaises(ValueError):
            failing_task()

        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        self.assertIn("Task failing_task failed", call_args[0][0])
        self.assertEqual(call_args[1]["extra"]["duration"], 1.5)
        self.assertEqual(call_args[1]["extra"]["status"], "failure")
        self.assertEqual(call_args[1]["extra"]["error"], "Test error")


@pytest.mark.django_db
class TestClearExpiredSessionsTask(TestCase):
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_successful_session_cleanup(self, mock_logger, mock_call_command):
        result = clear_expired_sessions_task()

        mock_call_command.assert_called_once_with("clearsessions", verbosity=0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "All expired sessions deleted")
        mock_logger.info.assert_any_call("Starting expired sessions cleanup")

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_command_error_handling(self, mock_logger, mock_call_command):
        mock_call_command.side_effect = CommandError("Command failed")

        result = clear_expired_sessions_task()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "CommandError")
        self.assertIn("Command failed", result["message"])
        mock_logger.error.assert_called()

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_unexpected_error_handling(self, mock_logger, mock_call_command):
        mock_call_command.side_effect = RuntimeError("Unexpected error")

        with self.assertRaises(RuntimeError):
            clear_expired_sessions_task()

        mock_logger.exception.assert_called_with(
            "Unexpected error in clear_expired_sessions"
        )


@pytest.mark.django_db
class TestClearAllCacheTask(TestCase):
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_successful_cache_cleanup(self, mock_logger, mock_call_command):
        result = clear_all_cache_task()

        mock_call_command.assert_called_once_with("clear_cache", verbosity=0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "All cache deleted")

    @patch("core.tasks.management.call_command")
    def test_cache_cleanup_command_error(self, mock_call_command):
        mock_call_command.side_effect = CommandError("Cache error")

        result = clear_all_cache_task()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "CommandError")


@pytest.mark.django_db
class TestClearDuplicateHistoryTask(TestCase):
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_duplicate_history_cleanup_with_defaults(
        self, mock_logger, mock_call_command
    ):
        result = clear_duplicate_history_task()

        mock_call_command.assert_called_once_with(
            "clean_duplicate_history", "--auto"
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("Duplicate history entries cleaned", result["message"])

    @patch("core.tasks.management.call_command")
    def test_duplicate_history_cleanup_with_parameters(self, mock_call_command):
        excluded_fields = ["field1", "field2"]
        minutes = 30

        result = clear_duplicate_history_task(
            excluded_fields=excluded_fields, minutes=minutes
        )

        mock_call_command.assert_called_once_with(
            "clean_duplicate_history",
            "-m",
            "30",
            "--excluded_fields",
            "field1",
            "field2",
            "--auto",
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["parameters"]["excluded_fields"], excluded_fields
        )
        self.assertEqual(result["parameters"]["minutes"], minutes)

    @patch("core.tasks.management.call_command")
    def test_duplicate_history_cleanup_command_error(self, mock_call_command):
        mock_call_command.side_effect = CommandError("History error")

        result = clear_duplicate_history_task()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "CommandError")


@pytest.mark.django_db
class TestClearOldHistoryTask(TestCase):
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_old_history_cleanup_with_default_days(
        self, mock_logger, mock_call_command
    ):
        result = clear_old_history_task()

        mock_call_command.assert_called_once_with(
            "clean_old_history", "--days", "365", "--auto"
        )
        self.assertEqual(result["status"], "success")
        self.assertIn(
            "Old history entries cleaned (older than 365 days)",
            result["message"],
        )

    @patch("core.tasks.management.call_command")
    def test_old_history_cleanup_with_custom_days(self, mock_call_command):
        result = clear_old_history_task(days=30)

        mock_call_command.assert_called_once_with(
            "clean_old_history", "--days", "30", "--auto"
        )
        self.assertEqual(result["status"], "success")


@pytest.mark.django_db
class TestClearExpiredNotificationsTask(TestCase):
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_expired_notifications_cleanup(
        self, mock_logger, mock_call_command
    ):
        result = clear_expired_notifications_task(days=30)

        mock_call_command.assert_called_once_with(
            "expire_notifications", "--days", "30"
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "Expired notifications deleted")

    @patch("core.tasks.management.call_command")
    def test_expired_notifications_with_default_days(self, mock_call_command):
        result = clear_expired_notifications_task()

        mock_call_command.assert_called_once_with(
            "expire_notifications", "--days", "365"
        )
        self.assertEqual(result["status"], "success")


@pytest.mark.django_db
class TestClearCartsForNoneUsersTask(TestCase):
    def setUp(self):
        self.user = UserAccountFactory()
        self.user_cart = Cart.objects.create(
            user=self.user, session_key="user_session"
        )
        self.null_cart1 = Cart.objects.create(
            user=None, session_key="null_session1"
        )
        self.null_cart2 = Cart.objects.create(
            user=None, session_key="null_session2"
        )

    @patch("core.tasks.logger")
    def test_clear_null_user_carts(self, mock_logger):
        result = clear_carts_for_none_users_task()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["carts_deleted"], 2)

        self.assertFalse(Cart.objects.filter(user=None).exists())
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

        mock_logger.info.assert_called()

    @patch("core.tasks.logger")
    def test_clear_null_user_carts_no_carts(self, mock_logger):
        Cart.objects.filter(user=None).delete()

        result = clear_carts_for_none_users_task()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["carts_deleted"], 0)

    @patch("core.tasks.transaction.atomic")
    @patch("core.tasks.logger")
    def test_clear_null_user_carts_database_error(
        self, mock_logger, mock_atomic
    ):
        mock_atomic.side_effect = Exception("Database error")

        with self.assertRaises(Exception):  # noqa: B017
            clear_carts_for_none_users_task()

        mock_logger.exception.assert_called_with(
            "Error clearing null user carts"
        )


@pytest.mark.django_db
class TestClearLogFilesTask(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.logs_path = os.path.join(self.temp_dir, "logs")
        os.makedirs(self.logs_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.os.listdir")
    @patch("core.tasks.os.path.isfile")
    @patch("core.tasks.os.path.getmtime")
    @patch("core.tasks.os.path.getsize")
    @patch("core.tasks.os.remove")
    @patch("core.tasks.logger")
    def test_clear_old_log_files(  # noqa: PLR0913
        self,
        mock_logger,
        mock_remove,
        mock_getsize,
        mock_getmtime,
        mock_isfile,
        mock_listdir,
        mock_exists,
        mock_join,
    ):
        mock_join.return_value = self.logs_path
        mock_exists.return_value = True
        mock_listdir.return_value = [
            "old_log.log",
            "recent_log.log",
            "not_a_file",
        ]

        old_time = (timezone.now() - timedelta(days=40)).timestamp()
        recent_time = (timezone.now() - timedelta(days=10)).timestamp()

        def mock_isfile_side_effect(path):
            return "not_a_file" not in path

        def mock_getmtime_side_effect(path):
            if "old_log.log" in path:
                return old_time
            return recent_time

        def mock_getsize_side_effect(path):
            return 1024 if "old_log.log" in path else 512

        mock_isfile.side_effect = mock_isfile_side_effect
        mock_getmtime.side_effect = mock_getmtime_side_effect
        mock_getsize.side_effect = mock_getsize_side_effect

        result = clear_log_files_task(days=30)

        self.assertEqual(result["status"], "success")
        self.assertIn("status", result)
        self.assertIn("deleted_count", result)
        mock_isfile.assert_called()
        mock_getmtime.assert_called()

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.logger")
    def test_clear_log_files_no_logs_directory(
        self, mock_logger, mock_exists, mock_join
    ):
        mock_join.return_value = "/nonexistent/logs"
        mock_exists.return_value = False

        result = clear_log_files_task()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "logs directory not found")
        mock_logger.warning.assert_called()

    @override_settings(BASE_DIR=Path(__file__).parent)
    @patch("core.tasks.os.path.join")
    @patch("core.tasks.os.path.exists")
    @patch("core.tasks.os.listdir")
    @patch("core.tasks.logger")
    def test_clear_log_files_os_error(
        self, mock_logger, mock_listdir, mock_exists, mock_join
    ):
        mock_join.return_value = self.logs_path
        mock_exists.return_value = True
        mock_listdir.side_effect = OSError("Permission denied")

        with self.assertRaises(OSError):
            clear_log_files_task()

        mock_logger.exception.assert_called()


@pytest.mark.django_db
class TestSendInactiveUserNotificationsTask(TestCase):
    def setUp(self):
        self.active_user = UserAccountFactory(
            last_login=timezone.now() - timedelta(days=30),
            is_active=True,
            email="active@example.com",
        )
        self.inactive_user = UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            first_name="John",
        )
        self.very_old_user = UserAccountFactory(
            last_login=timezone.now() - timedelta(days=100),
            is_active=True,
            email="old@example.com",
        )
        self.inactive_no_email = UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="",
        )

    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    @override_settings(
        NUXT_BASE_URL="https://example.com",
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_send_inactive_user_notifications_success(
        self, mock_logger, mock_render, mock_send_mail
    ):
        mock_render.return_value = "<html>Test email</html>"

        result = send_inactive_user_notifications()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["total_users"], 2)
        self.assertEqual(result["emails_sent"], 2)
        self.assertEqual(result["failed"], 0)

        self.assertEqual(mock_send_mail.call_count, 2)

        call_args = mock_send_mail.call_args_list[0][1]
        self.assertIn("We miss you!", call_args["subject"])
        self.assertEqual(call_args["from_email"], "noreply@example.com")

    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    def test_send_inactive_user_notifications_with_failures(
        self, mock_logger, mock_render, mock_send_mail
    ):
        mock_render.return_value = "<html>Test email</html>"
        mock_send_mail.side_effect = [
            Exception("SMTP error"),
            None,
        ]

        result = send_inactive_user_notifications()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["total_users"], 2)
        self.assertEqual(result["emails_sent"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["failed_details"]), 1)

    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    def test_send_inactive_user_notifications_too_many_failures(
        self, mock_logger, mock_render, mock_send_mail
    ):
        mock_render.return_value = "<html>Test email</html>"
        mock_send_mail.side_effect = Exception("SMTP error")

        for i in range(55):
            UserAccountFactory(
                last_login=timezone.now() - timedelta(days=70),
                is_active=True,
                email=f"inactive{i}@example.com",
            )

        result = send_inactive_user_notifications()

        self.assertEqual(result["emails_sent"], 0)
        self.assertEqual(len(result["failed_details"]), 10)
        mock_logger.error.assert_any_call(
            "Too many email failures, stopping task"
        )


@pytest.mark.django_db
class TestMonitorSystemHealthTask(TestCase):
    @patch("core.tasks.connections")
    @patch("core.tasks.cache")
    @patch("core.tasks.open", new_callable=mock_open)
    @patch("core.tasks.os.remove")
    @patch("core.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media")
    def test_monitor_system_health_all_passed(
        self, mock_logger, mock_remove, mock_file, mock_cache, mock_connections
    ):
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"

        result = monitor_system_health()

        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["checks"]["database"])
        self.assertTrue(result["checks"]["cache"])
        self.assertTrue(result["checks"]["storage"])
        self.assertEqual(len(result["errors"]), 0)

    @patch("core.tasks.connections")
    @patch("core.tasks.cache")
    @patch("core.tasks.open")
    @patch("core.tasks.send_mail")
    @patch("core.tasks.logger")
    @override_settings(
        MEDIA_ROOT="/tmp/media",
        DEFAULT_FROM_EMAIL="admin@example.com",
        ADMIN_EMAIL="admin@example.com",
    )
    def test_monitor_system_health_database_failure(
        self,
        mock_logger,
        mock_send_mail,
        mock_file,
        mock_cache,
        mock_connections,
    ):
        mock_connections.__getitem__.return_value.cursor.side_effect = (
            Exception("DB error")
        )

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"

        with self.assertRaises(  # noqa: B017
            Exception,
            msg="Critical system health check failed",
        ):
            monitor_system_health()

        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args[1]
        self.assertIn(
            "CRITICAL: System Health Check Failed", call_args["subject"]
        )

    @patch("core.tasks.connections")
    @patch("core.tasks.cache")
    @patch("core.tasks.open", new_callable=mock_open)
    @patch("core.tasks.os.remove")
    @patch("core.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media")
    def test_monitor_system_health_cache_failure(
        self, mock_logger, mock_remove, mock_file, mock_cache, mock_connections
    ):
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.side_effect = Exception("Cache error")

        result = monitor_system_health()

        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["checks"]["database"])
        self.assertFalse(result["checks"]["cache"])
        self.assertTrue(result["checks"]["storage"])
        self.assertGreater(len(result["errors"]), 0)

    @patch("core.tasks.connections")
    @patch("core.tasks.cache")
    @patch("core.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media")
    def test_monitor_system_health_storage_failure(
        self, mock_logger, mock_cache, mock_connections
    ):
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"

        mock_open_obj = mock_open()
        mock_open_obj.side_effect = OSError("Storage error")

        with patch("core.tasks.open", mock_open_obj):
            result = monitor_system_health()

        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["checks"]["database"])
        self.assertTrue(result["checks"]["cache"])
        self.assertFalse(result["checks"]["storage"])


@pytest.mark.django_db
class TestBackupDatabaseTask(TestCase):
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

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file_size"], 1024000)
        self.assertIn(
            "Database backup completed successfully", result["message"]
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

        result = backup_database_task()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "CommandError")
        self.assertIn("Backup failed", result["message"])

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

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["file_size"], 0)
        self.assertIsNone(result["backup_file"])


@pytest.mark.django_db
class TestScheduledDatabaseBackupTask(TestCase):
    @patch("core.tasks.backup_database_task")
    @patch("core.tasks.cleanup_old_backups")
    @patch("core.tasks.logger")
    def test_scheduled_backup_success(
        self, mock_logger, mock_cleanup, mock_backup
    ):
        mock_result = Mock()
        mock_result.result = {
            "status": "success",
            "message": "Backup completed",
        }
        mock_backup.apply.return_value = mock_result

        result = scheduled_database_backup()

        self.assertEqual(result["status"], "success")
        mock_backup.apply.assert_called_once()
        mock_cleanup.delay.assert_called_once_with(
            days=7, backup_dir="backups/scheduled"
        )

    @patch("core.tasks.backup_database_task")
    @patch("core.tasks.send_mail")
    @patch("core.tasks.logger")
    @override_settings(
        DEFAULT_FROM_EMAIL="admin@example.com", ADMIN_EMAIL="admin@example.com"
    )
    def test_scheduled_backup_failure(
        self, mock_logger, mock_send_mail, mock_backup
    ):
        mock_result = Mock()
        mock_result.result = {"status": "error", "message": "Backup failed"}
        mock_backup.apply.return_value = mock_result

        with self.assertRaises(Exception):  # noqa: B017
            scheduled_database_backup()

        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args[1]
        self.assertIn(
            "ALERT: Scheduled Database Backup Failed", call_args["subject"]
        )

    @patch("core.tasks.backup_database_task")
    @patch("core.tasks.send_mail")
    @patch("core.tasks.logger")
    def test_scheduled_backup_email_failure(
        self, mock_logger, mock_send_mail, mock_backup
    ):
        mock_result = Mock()
        mock_result.result = {"status": "error", "message": "Backup failed"}
        mock_backup.apply.return_value = mock_result

        mock_send_mail.side_effect = Exception("Email error")

        with self.assertRaises(Exception):  # noqa: B017
            scheduled_database_backup()

        mock_logger.error.assert_any_call(
            "Failed to send backup failure alert email: Email error"
        )


@pytest.mark.django_db
class TestCleanupOldBackupsTask(TestCase):
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
        mock_path.return_value.__truediv__.return_value = mock_backup_dir

        result = cleanup_old_backups(days=30, backup_dir="backups")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["total_size_freed"], 1024)
        old_file.unlink.assert_called_once()
        recent_file.unlink.assert_not_called()

    @patch("core.tasks.Path")
    @patch("core.tasks.logger")
    @override_settings(BASE_DIR="/test/base")
    def test_cleanup_old_backups_no_directory(self, mock_logger, mock_path):
        mock_backup_dir = Mock()
        mock_backup_dir.exists.return_value = False
        mock_path.return_value.__truediv__.return_value = mock_backup_dir

        result = cleanup_old_backups()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "backup directory not found")
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
        mock_path.return_value.__truediv__.return_value = mock_backup_dir

        result = cleanup_old_backups()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["deleted_count"], 0)
        mock_logger.error.assert_called()


class TestValidateTaskConfiguration(TestCase):
    @override_settings(
        DEFAULT_FROM_EMAIL="test@example.com",
        ADMIN_EMAIL="admin@example.com",
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
        if hasattr(settings, "ADMIN_EMAIL"):
            del settings.ADMIN_EMAIL

        with self.assertRaises(ImproperlyConfigured) as cm:
            validate_task_configuration()

        self.assertIn("Missing required settings", str(cm.exception))


@pytest.mark.django_db
class TestTaskIntegration(TestCase):
    def setUp(self):
        self.user = UserAccountFactory()
        self.cart = Cart.objects.create(user=None, session_key="test_session")

    @patch("core.tasks.management.call_command")
    def test_multiple_cleanup_tasks_workflow(self, mock_call_command):
        session_result = clear_expired_sessions_task()
        cache_result = clear_all_cache_task()
        cart_result = clear_carts_for_none_users_task()

        self.assertEqual(session_result["status"], "success")
        self.assertEqual(cache_result["status"], "success")
        self.assertEqual(cart_result["status"], "success")

        self.assertFalse(Cart.objects.filter(user=None).exists())

        expected_calls = ["clearsessions", "clear_cache"]
        actual_calls = [call[0][0] for call in mock_call_command.call_args_list]
        for expected_call in expected_calls:
            self.assertIn(expected_call, actual_calls)
