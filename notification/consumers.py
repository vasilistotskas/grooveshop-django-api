import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.serializers.json import DjangoJSONEncoder

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
            username = (
                self.user.username
                if not self.user.is_anonymous
                else "AnonymousUser"
            )
            logger.debug(f"User from scope: {username}")

            if self.user.is_anonymous:
                # Must accept the WebSocket handshake before sending a close
                # frame — the Channels protocol requires the handshake to
                # complete before application-level close codes are delivered.
                logger.warning("Anonymous user, closing connection")
                await self.accept()
                await self.close(code=4003)
            else:
                prefix = self._get_tenant_prefix()
                logger.debug(
                    f"Authenticated user: {self.user.username} "
                    f"(ID: {self.user.id})"
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
            logger.error(f"KeyError in connect: {e}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Unexpected error in connect: {e}")
            await self.close(code=4500)

    async def disconnect(self, code):
        logger.debug(
            f"NotificationConsumer disconnect called with code: {code}"
        )
        if not self.user:
            logger.debug("No user in disconnect")
            return

        if not self.user.is_anonymous:
            if self.group_name is None:
                # connect() raised before group_name was assigned —
                # nothing to discard.
                return
            logger.debug(f"Removing user from group: {self.group_name}")
            try:
                await self.channel_layer.group_discard(
                    self.group_name, self.channel_name
                )
            except Exception:
                logger.exception(
                    f"group_discard failed for group {self.group_name}"
                )

            if self.user.is_staff and self.admin_group_name:
                logger.debug("User is staff, removing from admins group")
                try:
                    await self.channel_layer.group_discard(
                        self.admin_group_name, self.channel_name
                    )
                except Exception:
                    logger.exception(
                        f"group_discard failed for group {self.admin_group_name}"
                    )

    async def send_notification(self, event):
        logger.debug(f"Sending notification: {event}")
        await self.send(text_data=json.dumps(event, cls=DjangoJSONEncoder))

    async def force_logout(self, event):
        """
        Handler for group messages of type ``force.logout``.

        Broadcast by ``user/signals.py`` after Knox tokens are revoked
        (password change, email change).  Closes the WebSocket with code
        4003 so connected clients know they must re-authenticate.
        """
        user_pk = getattr(self.user, "pk", "unknown")
        logger.info(f"Force-logout WebSocket for user {user_pk}")
        await self.close(code=4003)

    async def receive(self, text_data=None, bytes_data=None):
        # This is a server-push-only channel; client messages are ignored.
        logger.debug("Client message received and discarded (server-push only)")
