import json
from typing import override

from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationConsumer(AsyncWebsocketConsumer):
    user = None
    group_name = None

    @override
    async def connect(self):
        try:
            self.user = self.scope["user"]
            if self.user.is_anonymous:
                await self.close()
            else:
                self.group_name = f"user_{self.user.id}"
                await self.channel_layer.group_add(self.group_name, self.channel_name)

                if self.user.is_staff:
                    await self.channel_layer.group_add("admins", self.channel_name)

                await self.accept()
        except KeyError:
            await self.close()

    @override
    async def disconnect(self, close_code):
        if not self.user.is_anonymous:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

            if self.user.is_staff:
                await self.channel_layer.group_discard("admins", self.channel_name)

    async def send_notification(self, event):
        await self.send(text_data=json.dumps(event))
