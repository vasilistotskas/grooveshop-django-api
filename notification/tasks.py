import json

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

from notification.dataclasses import NotificationData


@shared_task(bind=True, name="Send Notification Task")
def send_notification_task(self, data: NotificationData, *args, **kwargs):
    translations_json = json.dumps(data.translations)
    translations_clean = json.loads(translations_json)

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"user_{data.user_id}",
        {
            "type": "send_notification",
            "user": data.user_id,
            "seen": data.seen,
            "link": data.link,
            "kind": data.kind,
            "translations": translations_clean,
        },
    )
