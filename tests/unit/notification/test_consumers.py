import json
from unittest.mock import AsyncMock, MagicMock

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
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
