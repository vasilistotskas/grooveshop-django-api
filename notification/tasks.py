from __future__ import absolute_import
from __future__ import unicode_literals

import json

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer


@shared_task(bind=True, name="Send Notification Task")
def send_notification_task(
    self,
    user: int,
    seen: bool,
    link: str,
    kind: str,
    translations: list,
    *args,
    **kwargs,
):
    translations_json = json.dumps(translations)
    translations_clean = json.loads(translations_json)

    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        "notifications",
        {
            "type": "notification.message",
            "user": user,
            "seen": seen,
            "link": link,
            "kind": kind,
            "translations": translations_clean,
        },
    )
