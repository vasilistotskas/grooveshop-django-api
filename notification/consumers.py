import json

from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await self.channel_layer.group_add("notifications", self.channel_name)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("notifications", self.channel_name)

    async def send_notification(self, event):
        user = event["user"]
        seen = event["seen"]
        link = event["link"]
        kind = event["kind"]
        translations = event["translations"]

        await self.send(
            text_data=json.dumps(
                {
                    "type": "notification",
                    "user": user,
                    "seen": seen,
                    "link": link,
                    "kind": kind,
                    "translations": translations,
                }
            )
        )
