"""Centralised channel-layer group helpers.

Every piece of code that sends to or joins a notification group MUST use
these helpers so that the consumer and the senders share exactly the same
name format.  The format is:

    tenant_{schema_name}_user_{user_id}

The schema_name segment isolates tenants inside the shared Redis channel
layer, so a message intended for webside user 42 cannot accidentally reach
a homonymous user on another tenant.
"""

from __future__ import annotations

import asyncio
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer
from django.conf import settings
from django.db import connection


def user_group(schema_name: str, user_id: int | str) -> str:
    """Return the per-user channel-layer group name for *schema_name*."""
    return f"tenant_{schema_name}_user_{user_id}"


async def _group_send_within_deadline(
    layer: BaseChannelLayer, group: str, message: dict[str, Any]
) -> None:
    async with asyncio.timeout(settings.NOTIFICATION_PUSH_TIMEOUT_SECONDS):
        await layer.group_send(group, message)


def send_to_user_group(
    layer: BaseChannelLayer, user_id: int | str, message: dict[str, Any]
) -> None:
    """Send *message* to the user's group in the schema now active.

    Raises ``TimeoutError`` when the layer has not taken the message
    within ``NOTIFICATION_PUSH_TIMEOUT_SECONDS``: the push is best
    effort, and a caller that blocks a worker or a request on it must
    not wait for a Redis that stopped answering.
    """
    schema_name = getattr(connection, "schema_name", None) or "public"
    async_to_sync(_group_send_within_deadline)(
        layer, user_group(schema_name, user_id), message
    )
