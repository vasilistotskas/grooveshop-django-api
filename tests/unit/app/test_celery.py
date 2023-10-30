import os
from unittest.mock import Mock
from unittest.mock import patch

from django.test import TestCase

from app import celery


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

    @patch.dict(os.environ, {"DEBUG": "1"})
    @patch("app.celery.create_celery_app")
    def test_debug_task_with_debug_true(self, mock_create_celery_app):
        celery.app = mock_create_celery_app.return_value
        celery.debug_task = Mock()

        celery.app = celery.create_celery_app()
        celery.debug_task()

        self.assertTrue(celery.debug_task.called)

    @patch.dict(os.environ, {"DEBUG": "0"})
    @patch("app.celery.create_celery_app")
    def test_debug_task_with_debug_false(self, mock_create_celery_app):
        celery.app = mock_create_celery_app.return_value
        celery.debug_task = Mock()

        celery.app = celery.create_celery_app()
        celery.debug_task()

        self.assertTrue(celery.debug_task.called)

    def tearDown(self) -> None:
        super().tearDown()
        celery.app = None
        celery.debug_task = None
