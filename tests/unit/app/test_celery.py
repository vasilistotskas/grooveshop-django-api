import os
from datetime import datetime
from datetime import timedelta
from os import makedirs
from os import path
from os import remove
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from app import celery
from core.tasks import cleanup_log_files_task


class CeleryConfigTestCase(TestCase):
    @patch("app.celery.Celery")
    def test_create_celery_app(self, mock_celery):
        mock_celery_instance = mock_celery.return_value

        app = celery.create_celery_app()

        self.assertTrue(mock_celery.called)
        self.assertEqual(app.conf.enable_utc, False)
        self.assertEqual(app.conf.timezone, mock_celery_instance.conf.timezone)
        self.assertEqual(
            app.conf.beat_schedule,
            {
                "clear_sessions_for_none_users_task-every-week": {
                    "task": "core.tasks.clear_sessions_for_none_users_task",
                    "schedule": celery.crontab(minute=0, hour=0, day_of_week="mon"),
                },
            },
        )
        self.assertTrue(app.autodiscover_tasks.called)

    @patch.dict(os.environ, {"DEBUG": "True"})
    @patch("app.celery.create_celery_app")
    def test_debug_task_with_debug_true(self, mock_create_celery_app):
        celery.app = mock_create_celery_app.return_value
        celery.debug_task = Mock()

        celery.app = celery.create_celery_app()
        celery.debug_task()

        self.assertTrue(celery.debug_task.called)

    @patch.dict(os.environ, {"DEBUG": "False"})
    @patch("app.celery.create_celery_app")
    def test_debug_task_with_debug_false(self, mock_create_celery_app):
        celery.app = mock_create_celery_app.return_value
        celery.debug_task = Mock()

        celery.app = celery.create_celery_app()
        celery.debug_task()

        self.assertTrue(celery.debug_task.called)

    @patch("app.celery.get_channel_layer")
    @patch("os.getenv")
    @patch.dict(os.environ, {"DEBUG": "True"})
    def test_debug_task_notification_with_debug_true(
        self, mock_getenv, mock_get_channel_layer
    ):
        mock_getenv.return_value = "True"
        mock_channel_layer = mock_get_channel_layer.return_value
        mock_channel_layer.group_send = AsyncMock()

        celery.debug_task_notification()

        mock_channel_layer.group_send.assert_called_once_with(
            "notifications",
            {
                "type": "send_notification",
                "user": 1,
                "seen": False,
                "link": "https://www.google.com",
                "kind": "info",
                "translations": [
                    {
                        "en": {
                            "message": "This is a test notification",
                        }
                    },
                ],
            },
        )

    def tearDown(self) -> None:
        super().tearDown()
        celery.app = None
        celery.debug_task = None


class CleanupLogFilesTaskTest(TestCase):
    days = 30

    def setUp(self):
        self.logs_path = path.join(settings.BASE_DIR, "logs")
        makedirs(self.logs_path, exist_ok=True)

        # Create some log files, some of which are older than 30 days
        self.old_file = path.join(self.logs_path, "logs_01-01-2000.log")
        self.new_file = path.join(self.logs_path, "logs_01-01-3000.log")

        with open(self.old_file, "w") as f:
            f.write("Old file")

        with open(self.new_file, "w") as f:
            f.write("New file")

        # Change the modification time of the old file to be more than 30 days ago
        old_time = (datetime.now() - timedelta(days=31)).timestamp()
        os.utime(self.old_file, (old_time, old_time))

    def tearDown(self):
        # Clean up the created files
        if path.exists(self.old_file):
            remove(self.old_file)
        if path.exists(self.new_file):
            remove(self.new_file)

    @patch("core.tasks.logger")
    def test_cleanup_log_files_task(self, mock_logger):
        # Call the task with an integer argument for `days`
        cleanup_log_files_task(self.days)

        # Check that the old file has been deleted and the new file is still there
        self.assertFalse(path.exists(self.old_file))
        self.assertTrue(path.exists(self.new_file))

        # Check that the logger was called with the correct message
        mock_logger.info.assert_called_once_with(
            "Removed log files older than 30 days."
        )

    def test_does_not_remove_recent_logs(self):
        # Create a recent log file
        recent_file = path.join(self.logs_path, "logs_01-01-2021.log")
        with open(recent_file, "w") as f:
            f.write("Recent file")

        # Change the modification time of the recent file to be less than `days` days ago
        recent_time = (datetime.now() - timedelta(days=self.days - 1)).timestamp()
        os.utime(recent_file, (recent_time, recent_time))

        # Call the task with the class variable `days`
        cleanup_log_files_task(self.days)

        # Check that the recent file is still there
        self.assertTrue(path.exists(recent_file))

        # Clean up the created file
        if path.exists(recent_file):
            remove(recent_file)
