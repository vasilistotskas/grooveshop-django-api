import datetime
import os
from os import makedirs, path, remove
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from core import celery
from core.tasks import clear_log_files_task


class CeleryConfigTestCase(TestCase):
    @patch("core.celery.Celery")
    def test_create_celery_app(self, mock_celery):
        mock_celery_instance = mock_celery.return_value

        app = celery.create_celery_app()

        self.assertTrue(mock_celery.called)
        self.assertEqual(app.conf.enable_utc, False)
        self.assertEqual(app.conf.timezone, mock_celery_instance.conf.timezone)
        self.assertTrue(app.autodiscover_tasks.called)

    def tearDown(self):
        celery.app = None
        celery.debug_task = None
        super().tearDown()


class CleanupLogFilesTaskTest(TestCase):
    days = 30

    def setUp(self):
        self.logs_path = path.join(settings.BASE_DIR, "logs")
        makedirs(self.logs_path, exist_ok=True)

        self.old_file = path.join(self.logs_path, "logs_01-01-2000.log")
        self.new_file = path.join(self.logs_path, "logs_01-01-3000.log")

        with open(self.old_file, "w") as f:
            f.write("Old file")

        with open(self.new_file, "w") as f:
            f.write("New file")

        old_time = (
            datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=31)
        ).timestamp()
        os.utime(self.old_file, (old_time, old_time))

    def tearDown(self):
        if path.exists(self.old_file):
            remove(self.old_file)
        if path.exists(self.new_file):
            remove(self.new_file)
        super().tearDown()

    def test_cleanup_log_files_task(self):
        clear_log_files_task(self.days)

        self.assertFalse(path.exists(self.old_file))
        self.assertTrue(path.exists(self.new_file))

    def test_does_not_remove_recent_logs(self):
        recent_file = path.join(self.logs_path, "logs_01-01-2021.log")
        with open(recent_file, "w") as f:
            f.write("Recent file")

        recent_time = (
            datetime.datetime.now(tz=datetime.UTC)
            - datetime.timedelta(days=self.days - 1)
        ).timestamp()
        os.utime(recent_file, (recent_time, recent_time))

        clear_log_files_task(self.days)

        self.assertTrue(path.exists(recent_file))

        if path.exists(recent_file):
            remove(recent_file)
