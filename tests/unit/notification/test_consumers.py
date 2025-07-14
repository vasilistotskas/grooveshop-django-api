import json
from unittest.mock import AsyncMock, MagicMock, patch

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase

from asgi import application
from notification.consumers import NotificationConsumer

User = get_user_model()


class TestNotificationConsumer(TransactionTestCase):
    async def test_connect_anonymous_user(self):
        communicator = WebsocketCommunicator(application, "ws/notifications/")
        connected, _ = await communicator.connect()
        assert not connected
        await communicator.disconnect()

    async def test_consumer_directly(self):
        user = await self.create_test_user()

        consumer = NotificationConsumer()
        consumer.scope = {"user": user}
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.channel_name = "test_channel"
        consumer.accept = AsyncMock()
        consumer.send = AsyncMock()
        consumer.close = AsyncMock()

        await consumer.connect()

        consumer.channel_layer.group_add.assert_called_with(
            f"user_{user.id}", consumer.channel_name
        )

        consumer.accept.assert_called_once()

        test_message = {"text": "Hello, world!"}
        await consumer.receive(text_data=json.dumps(test_message))

        consumer.send.assert_called_with(
            text_data=json.dumps(
                {
                    "type": "echo.message",
                    "message": test_message,
                    "from": user.username,
                }
            )
        )

        consumer.send.reset_mock()
        await consumer.receive(text_data="not json")

        consumer.send.assert_called_with(
            text_data=json.dumps(
                {"type": "error", "message": "Invalid JSON format"}
            )
        )

        await consumer.disconnect(1000)

        consumer.channel_layer.group_discard.assert_any_call(
            f"user_{user.id}", consumer.channel_name
        )

    @database_sync_to_async
    def create_test_user(self):
        return User.objects.create_user(
            username="testuser", email="test@test.com", password="password123"
        )

    @database_sync_to_async
    def create_staff_user(self):
        user = User.objects.create_user(
            username="staffuser", email="staff@test.com", password="password123"
        )
        user.is_staff = True
        user.save()
        return user

    async def test_staff_user_groups(self):
        staff_user = await self.create_staff_user()

        consumer = NotificationConsumer()
        consumer.scope = {"user": staff_user}
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.channel_name = "test_channel"
        consumer.accept = AsyncMock()
        consumer.close = AsyncMock()

        await consumer.connect()

        consumer.channel_layer.group_add.assert_any_call(
            f"user_{staff_user.id}", consumer.channel_name
        )
        consumer.channel_layer.group_add.assert_any_call(
            "admins", consumer.channel_name
        )

        await consumer.disconnect(1000)

        consumer.channel_layer.group_discard.assert_any_call(
            f"user_{staff_user.id}", consumer.channel_name
        )
        consumer.channel_layer.group_discard.assert_any_call(
            "admins", consumer.channel_name
        )

    async def test_send_notification(self):
        consumer = NotificationConsumer()
        consumer.send = AsyncMock()

        notification = {
            "type": "send_notification",
            "message": "Test notification",
            "level": "info",
        }

        await consumer.send_notification(notification)

        consumer.send.assert_called_once_with(
            text_data=json.dumps(notification)
        )

    async def test_connect_error_handling(self):
        consumer = NotificationConsumer()
        consumer.close = AsyncMock()

        consumer.scope = {}
        await consumer.connect()
        consumer.close.assert_called_with(code=4000)

        consumer.close.reset_mock()
        consumer.scope = {"user": "not a user object"}
        await consumer.connect()
        consumer.close.assert_called_with(code=4500)

    @patch("notification.consumers.logger")
    async def test_connect_with_logging_authenticated_user(self, mock_logger):
        user = await self.create_test_user()

        consumer = NotificationConsumer()
        consumer.scope = {"user": user}
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_name = "test_channel"
        consumer.accept = AsyncMock()

        await consumer.connect()

        mock_logger.debug.assert_any_call("NotificationConsumer connect called")
        mock_logger.debug.assert_any_call(f"User from scope: {user.username}")
        mock_logger.debug.assert_any_call(
            f"Authenticated user: {user.username} (ID: {user.id})"
        )
        mock_logger.debug.assert_any_call(
            f"Adding user to group: user_{user.id}"
        )
        mock_logger.debug.assert_any_call("Accepting connection")
        mock_logger.debug.assert_any_call("Connection accepted")

        self.assertEqual(consumer.user, user)
        self.assertEqual(consumer.group_name, f"user_{user.id}")

    @patch("notification.consumers.logger")
    async def test_connect_with_logging_anonymous_user(self, mock_logger):
        anonymous_user = AnonymousUser()

        consumer = NotificationConsumer()
        consumer.scope = {"user": anonymous_user}
        consumer.close = AsyncMock()

        await consumer.connect()

        mock_logger.debug.assert_any_call("NotificationConsumer connect called")
        mock_logger.debug.assert_any_call("User from scope: AnonymousUser")
        mock_logger.warning.assert_called_with(
            "Anonymous user, closing connection"
        )

        consumer.close.assert_called_with(code=4003)

    @patch("notification.consumers.logger")
    async def test_connect_with_logging_staff_user(self, mock_logger):
        staff_user = await self.create_staff_user()

        consumer = NotificationConsumer()
        consumer.scope = {"user": staff_user}
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_add = AsyncMock()
        consumer.channel_name = "test_channel"
        consumer.accept = AsyncMock()

        await consumer.connect()

        mock_logger.debug.assert_any_call(
            "User is staff, adding to admins group"
        )

        consumer.channel_layer.group_add.assert_any_call(
            f"user_{staff_user.id}", "test_channel"
        )
        consumer.channel_layer.group_add.assert_any_call(
            "admins", "test_channel"
        )

    @patch("notification.consumers.logger")
    async def test_connect_keyerror_handling(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.scope = {}
        consumer.close = AsyncMock()

        await consumer.connect()

        mock_logger.error.assert_called()
        consumer.close.assert_called_with(code=4000)

    @patch("notification.consumers.logger")
    async def test_connect_exception_handling(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.scope = {"user": "invalid_user_object"}
        consumer.close = AsyncMock()

        await consumer.connect()

        mock_logger.exception.assert_called()
        consumer.close.assert_called_with(code=4500)

    @patch("notification.consumers.logger")
    async def test_disconnect_with_logging_no_user(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.user = None

        await consumer.disconnect(1000)

        mock_logger.debug.assert_any_call(
            "NotificationConsumer disconnect called with code: 1000"
        )
        mock_logger.debug.assert_any_call("No user in disconnect")

    @patch("notification.consumers.logger")
    async def test_disconnect_with_logging_authenticated_user(
        self, mock_logger
    ):
        user = await self.create_test_user()

        consumer = NotificationConsumer()
        consumer.user = user
        consumer.group_name = f"user_{user.id}"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.channel_name = "test_channel"

        await consumer.disconnect(1000)

        mock_logger.debug.assert_any_call(
            "NotificationConsumer disconnect called with code: 1000"
        )
        mock_logger.debug.assert_any_call(
            f"Removing user from group: user_{user.id}"
        )

        consumer.channel_layer.group_discard.assert_called_with(
            f"user_{user.id}", "test_channel"
        )

    @patch("notification.consumers.logger")
    async def test_disconnect_with_logging_staff_user(self, mock_logger):
        staff_user = await self.create_staff_user()

        consumer = NotificationConsumer()
        consumer.user = staff_user
        consumer.group_name = f"user_{staff_user.id}"
        consumer.channel_layer = MagicMock()
        consumer.channel_layer.group_discard = AsyncMock()
        consumer.channel_name = "test_channel"

        await consumer.disconnect(1000)

        mock_logger.debug.assert_any_call(
            "User is staff, removing from admins group"
        )

        consumer.channel_layer.group_discard.assert_any_call(
            f"user_{staff_user.id}", "test_channel"
        )
        consumer.channel_layer.group_discard.assert_any_call(
            "admins", "test_channel"
        )

    @patch("notification.consumers.logger")
    async def test_disconnect_with_anonymous_user(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.user = AnonymousUser()

        await consumer.disconnect(1000)

        mock_logger.debug.assert_any_call(
            "NotificationConsumer disconnect called with code: 1000"
        )

    @patch("notification.consumers.logger")
    async def test_send_notification_with_logging(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.send = AsyncMock()

        event = {"type": "test", "message": "Test notification"}
        await consumer.send_notification(event)

        mock_logger.debug.assert_called_with(f"Sending notification: {event}")
        consumer.send.assert_called_with(text_data=json.dumps(event))

    @patch("notification.consumers.logger")
    async def test_receive_with_logging_valid_json(self, mock_logger):
        user = await self.create_test_user()

        consumer = NotificationConsumer()
        consumer.user = user
        consumer.send = AsyncMock()

        test_data = {"message": "test"}
        await consumer.receive(text_data=json.dumps(test_data))

        mock_logger.debug.assert_called_with(
            f"Received message: {json.dumps(test_data)}"
        )

        expected_response = {
            "type": "echo.message",
            "message": test_data,
            "from": user.username,
        }
        consumer.send.assert_called_with(
            text_data=json.dumps(expected_response)
        )

    @patch("notification.consumers.logger")
    async def test_receive_with_logging_invalid_json(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.send = AsyncMock()

        invalid_json = "not valid json"
        await consumer.receive(text_data=invalid_json)

        mock_logger.debug.assert_called_with(
            f"Received message: {invalid_json}"
        )
        mock_logger.error.assert_called_with(
            f"Invalid JSON received: {invalid_json}"
        )

        expected_response = {"type": "error", "message": "Invalid JSON format"}
        consumer.send.assert_called_with(
            text_data=json.dumps(expected_response)
        )

    @patch("notification.consumers.logger")
    async def test_receive_with_anonymous_user(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.user = None
        consumer.send = AsyncMock()

        test_data = {"message": "test"}
        await consumer.receive(text_data=json.dumps(test_data))

        expected_response = {
            "type": "echo.message",
            "message": test_data,
            "from": "anonymous",
        }
        consumer.send.assert_called_with(
            text_data=json.dumps(expected_response)
        )

    @patch("notification.consumers.logger")
    async def test_receive_with_anonymous_user_object(self, mock_logger):
        consumer = NotificationConsumer()
        consumer.user = AnonymousUser()
        consumer.send = AsyncMock()

        test_data = {"message": "test"}
        await consumer.receive(text_data=json.dumps(test_data))

        expected_response = {
            "type": "echo.message",
            "message": test_data,
            "from": "anonymous",
        }
        consumer.send.assert_called_with(
            text_data=json.dumps(expected_response)
        )
