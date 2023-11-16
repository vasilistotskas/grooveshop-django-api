import os
from unittest.mock import AsyncMock
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
