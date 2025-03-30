from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer


@shared_task(bind=True, name="Send Notification Task")
def send_notification_task(self, data: dict, *args, **kwargs):
    translations_clean = data["translations"]

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"user_{data['user_id']}",
        {
            "type": "send_notification",
            "user": data["user_id"],
            "seen": data["seen"],
            "link": data["link"],
            "kind": data["kind"],
            "translations": translations_clean,
        },
    )
