from __future__ import absolute_import
from __future__ import unicode_literals

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer


@shared_task
def send_notification_task(
    user: int, seen: bool, link: str, kind: str, translations: list
):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "notifications",
        {
            "type": "notification",
            "kind": kind,
            "user": user,
            "seen": seen,
            "link": link,
            "translations": translations,
        },
    )
