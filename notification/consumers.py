import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger(__name__)

User = get_user_model()


class NotificationConsumer(AsyncWebsocketConsumer):
    user: AbstractBaseUser | AnonymousUser | None = None
    group_name: str | None = None
    admin_group_name: str | None = None

    def _get_tenant_prefix(self) -> str:
        tenant = self.scope.get("tenant")
        if tenant:
            return tenant.schema_name
        return "public"

    async def connect(self):
        try:
            logger.debug("NotificationConsumer connect called")
            self.user = self.scope["user"]
            logger.debug(
                f"User from scope: {self.user.username if not self.user.is_anonymous else 'AnonymousUser'}"
            )

            if self.user.is_anonymous:
                logger.warning("Anonymous user, closing connection")
                await self.close(code=4003)
            else:
                prefix = self._get_tenant_prefix()
                logger.debug(
                    f"Authenticated user: {self.user.username} (ID: {self.user.id})"
                )
                self.group_name = f"tenant_{prefix}_user_{self.user.id}"

                logger.debug(f"Adding user to group: {self.group_name}")
                await self.channel_layer.group_add(
                    self.group_name, self.channel_name
                )

                if self.user.is_staff:
                    self.admin_group_name = f"tenant_{prefix}_admins"
                    logger.debug("User is staff, adding to admins group")
                    await self.channel_layer.group_add(
                        self.admin_group_name, self.channel_name
                    )

                logger.debug("Accepting connection")
                await self.accept()
                logger.debug("Connection accepted")
        except KeyError as e:
            logger.error(f"KeyError in connect: {e!s}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Unexpected error in connect: {e!s}")
            await self.close(code=4500)

    async def disconnect(self, close_code):
        logger.debug(
            f"NotificationConsumer disconnect called with code: {close_code}"
        )
        if not self.user:
            logger.debug("No user in disconnect")
            return

        if not self.user.is_anonymous:
            logger.debug(f"Removing user from group: {self.group_name}")
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

            if self.user.is_staff and self.admin_group_name:
                logger.debug("User is staff, removing from admins group")
                await self.channel_layer.group_discard(
                    self.admin_group_name, self.channel_name
                )

    async def send_notification(self, event):
        logger.debug(f"Sending notification: {event}")
        await self.send(text_data=json.dumps(event))

    async def receive(self, text_data=None, bytes_data=None):
        logger.debug(f"Received message: {text_data}")
        try:
            data = json.loads(text_data or "")
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "echo.message",
                        "message": data,
                        "from": self.user.username
                        if self.user and not self.user.is_anonymous
                        else "anonymous",
                    }
                )
            )
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {text_data}")
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": "Invalid JSON format"}
                )
            )
