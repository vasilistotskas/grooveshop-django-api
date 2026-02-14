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
from django.test import override_settings
from django.utils import timezone

from cart.models import Cart
from core.tasks import (
    MonitoredTask,
    backup_database_task,
    cleanup_abandoned_carts,
    cleanup_old_backups,
    cleanup_old_guest_carts,
    clear_all_cache_task,
    clear_duplicate_history_task,
    clear_expired_notifications_task,
    clear_expired_sessions_task,
    clear_development_log_files_task,
    clear_old_history_task,
    monitor_system_health,
    scheduled_database_backup,
    send_inactive_user_notifications,
    validate_task_configuration,
)
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestMonitoredTask:
    @patch("core.tasks.logger")
    def test_on_success_logs_completion(self, mock_logger):
        task = MonitoredTask()
        task.name = "test_task"

        task.on_success(retval="success", task_id="12345", args=(), kwargs={})

        mock_logger.info.assert_called_once_with(
            "Task test_task completed successfully. Task ID: 12345"
        )

    @patch("core.tasks.logger")
    def test_on_failure_logs_error(self, mock_logger):
        task = MonitoredTask()
        task.name = "test_task"

        exception = Exception("Test error")
        task.on_failure(
            exc=exception, task_id="12345", args=(), kwargs={}, einfo=None
        )

        mock_logger.error.assert_called_once_with(
            "Task test_task failed. Task ID: 12345, Error: Test error"
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
    @patch("core.tasks.logger")
    def test_command_error_handling(self, mock_logger, mock_call_command):
        mock_call_command.side_effect = CommandError("Command failed")

        result = clear_expired_sessions_task()

        assert result["status"] == "error"
        assert result["error_type"] == "CommandError"
        assert "Command failed" in result["message"]
        mock_logger.error.assert_called()

    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_unexpected_error_handling(self, mock_logger, mock_call_command):
        mock_call_command.side_effect = RuntimeError("Unexpected error")

        with pytest.raises(RuntimeError):
            clear_expired_sessions_task()

        mock_logger.exception.assert_called_with(
            "Unexpected error in clear_expired_sessions"
        )


@pytest.mark.django_db
class TestClearAllCacheTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_successful_cache_cleanup(self, mock_logger, mock_call_command):
        result = clear_all_cache_task()

        mock_call_command.assert_called_once_with("clear_cache", verbosity=0)
        assert result["status"] == "success"
        assert result["message"] == "Cache cleared by prefix"

    @patch("core.tasks.management.call_command")
    def test_cache_cleanup_command_error(self, mock_call_command):
        mock_call_command.side_effect = CommandError("Cache error")

        result = clear_all_cache_task()

        assert result["status"] == "error"
        assert result["error_type"] == "CommandError"


@pytest.mark.django_db
class TestClearDuplicateHistoryTask:
    @patch("core.tasks.management.call_command")
    @patch("core.tasks.logger")
    def test_duplicate_history_cleanup_with_defaults(
        self, mock_logger, mock_call_command
    ):
        result = clear_duplicate_history_task()

        mock_call_command.assert_called_once_with("clean_duplicate_history")
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
    def test_duplicate_history_cleanup_command_error(self, mock_call_command):
        mock_call_command.side_effect = CommandError("History error")

        result = clear_duplicate_history_task()

        assert result["status"] == "error"
        assert result["error_type"] == "CommandError"


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


@pytest.mark.django_db(transaction=True)
class TestCleanupAbandonedCartsTask:
    def test_cleanup_abandoned_carts_removes_old_empty_carts(self, db):
        from cart.factories import CartItemFactory

        user = UserAccountFactory()
        user_cart = Cart.objects.create(user=user)

        old_empty_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=old_empty_cart.id).update(
            last_activity=timezone.now() - timedelta(days=10)
        )
        old_empty_cart.refresh_from_db()

        recent_empty_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=recent_empty_cart.id).update(
            last_activity=timezone.now() - timedelta(days=3)
        )
        recent_empty_cart.refresh_from_db()

        old_cart_with_items = Cart.objects.create(user=None)
        Cart.objects.filter(id=old_cart_with_items.id).update(
            last_activity=timezone.now() - timedelta(days=10)
        )
        old_cart_with_items.refresh_from_db()

        CartItemFactory(cart=old_cart_with_items)

        result = cleanup_abandoned_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 1

        assert not Cart.objects.filter(id=old_empty_cart.id).exists()

        assert Cart.objects.filter(id=recent_empty_cart.id).exists()

        assert Cart.objects.filter(id=user_cart.id).exists()

        assert Cart.objects.filter(id=old_cart_with_items.id).exists()

    def test_cleanup_abandoned_carts_no_carts_to_delete(self, db):
        Cart.objects.filter(
            user=None,
            items__isnull=True,
            last_activity__lt=timezone.now() - timedelta(days=7),
        ).delete()

        result = cleanup_abandoned_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 0


@pytest.mark.django_db(transaction=True)
class TestCleanupOldGuestCartsTask:
    def test_cleanup_old_guest_carts_removes_old_carts(self, db):
        user = UserAccountFactory()
        user_cart = Cart.objects.create(user=user)

        very_old_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=very_old_cart.id).update(
            last_activity=timezone.now() - timedelta(days=35)
        )
        very_old_cart.refresh_from_db()

        recent_cart = Cart.objects.create(user=None)
        Cart.objects.filter(id=recent_cart.id).update(
            last_activity=timezone.now() - timedelta(days=15)
        )
        recent_cart.refresh_from_db()

        result = cleanup_old_guest_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 1

        assert not Cart.objects.filter(id=very_old_cart.id).exists()

        assert Cart.objects.filter(id=recent_cart.id).exists()

        assert Cart.objects.filter(id=user_cart.id).exists()

    def test_cleanup_old_guest_carts_no_carts_to_delete(self, db):
        Cart.objects.filter(
            user=None, last_activity__lt=timezone.now() - timedelta(days=30)
        ).delete()

        result = cleanup_old_guest_carts()

        assert result["status"] == "success"
        assert result["deleted_count"] == 0


@pytest.mark.django_db
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
            if path == "/.dockerenv":
                return True
            elif path == self.logs_path:
                return True
            return False

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
            if path == "/.dockerenv":
                return True
            elif path == self.logs_path:
                return True
            return False

        mock_exists.side_effect = mock_exists_side_effect
        mock_join.return_value = self.logs_path
        mock_listdir.side_effect = OSError("Permission denied")

        with pytest.raises(OSError):
            clear_development_log_files_task()

        mock_logger.exception.assert_called()


@pytest.mark.django_db(transaction=True)
class TestSendInactiveUserNotificationsTask:
    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    @override_settings(
        NUXT_BASE_URL="https://example.com",
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_send_inactive_user_notifications_success(
        self, mock_logger, mock_render, mock_send_mail, db
    ):
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=30),
            is_active=True,
            email="active@example.com",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            first_name="John",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=100),
            is_active=True,
            email="old@example.com",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="",
        )

        mock_render.return_value = "<html>Test email</html>"

        result = send_inactive_user_notifications()

        assert result["status"] == "completed"
        assert result["total_users"] == 2
        assert result["emails_sent"] == 2
        assert result["failed"] == 0

        assert mock_send_mail.call_count == 2

        call_args = mock_send_mail.call_args_list[0][1]
        assert "We miss you!" in call_args["subject"]
        assert call_args["from_email"] == "noreply@example.com"

    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    def test_send_inactive_user_notifications_with_failures(
        self, mock_logger, mock_render, mock_send_mail, db
    ):
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=70),
            is_active=True,
            email="inactive@example.com",
            first_name="John",
        )
        UserAccountFactory(
            last_login=timezone.now() - timedelta(days=100),
            is_active=True,
            email="old@example.com",
        )

        mock_render.return_value = "<html>Test email</html>"
        mock_send_mail.side_effect = [
            Exception("SMTP error"),
            None,
        ]

        result = send_inactive_user_notifications()

        assert result["status"] == "completed"
        assert result["total_users"] == 2
        assert result["emails_sent"] == 1
        assert result["failed"] == 1
        assert len(result["failed_details"]) == 1

    @patch("core.tasks.send_mail")
    @patch("core.tasks.render_to_string")
    @patch("core.tasks.logger")
    def test_send_inactive_user_notifications_too_many_failures(
        self, mock_logger, mock_render, mock_send_mail, db
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

        assert result["emails_sent"] == 0
        assert len(result["failed_details"]) == 10
        mock_logger.error.assert_any_call(
            "Too many email failures, stopping task"
        )


@pytest.mark.django_db
class TestMonitorSystemHealthTask:
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

        assert result["status"] == "healthy"
        assert result["checks"]["database"] is True
        assert result["checks"]["cache"] is True
        assert result["checks"]["storage"] is True
        assert len(result["errors"]) == 0

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

        with pytest.raises(
            Exception, match="Critical system health check failed"
        ):
            monitor_system_health()

        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args[1]
        assert "CRITICAL: System Health Check Failed" in call_args["subject"]

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

        assert result["status"] == "degraded"
        assert result["checks"]["database"] is True
        assert result["checks"]["cache"] is False
        assert result["checks"]["storage"] is True
        assert len(result["errors"]) > 0

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

        assert result["status"] == "degraded"
        assert result["checks"]["database"] is True
        assert result["checks"]["cache"] is True
        assert result["checks"]["storage"] is False


@pytest.mark.django_db
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

        result = backup_database_task()

        assert result["status"] == "error"
        assert result["error_type"] == "CommandError"
        assert "Backup failed" in result["result_message"]

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


@pytest.mark.django_db
class TestScheduledDatabaseBackupTask:
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

        assert result["status"] == "success"
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

        with pytest.raises(Exception):
            scheduled_database_backup()

        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args[1]
        assert "ALERT: Scheduled Database Backup Failed" in call_args["subject"]

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

        with pytest.raises(Exception):
            scheduled_database_backup()

        mock_logger.error.assert_any_call(
            "Failed to send backup failure alert email: Email error"
        )


@pytest.mark.django_db
class TestCleanupOldBackupsTask:
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


@pytest.mark.django_db
class TestValidateTaskConfiguration:
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

        with pytest.raises(
            ImproperlyConfigured, match="Missing required settings"
        ):
            validate_task_configuration()


@pytest.mark.django_db(transaction=True)
class TestTaskIntegration:
    @patch("core.tasks.management.call_command")
    def test_multiple_cleanup_tasks_workflow(self, mock_call_command, db):
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
