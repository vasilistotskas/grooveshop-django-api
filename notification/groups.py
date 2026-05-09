"""Centralised channel-layer group name helpers.

Every piece of code that sends to or joins a notification group MUST use
these helpers so that the consumer and the senders share exactly the same
name format.  The format is:

    tenant_{schema_name}_user_{user_id}
    tenant_{schema_name}_admins

The schema_name segment isolates tenants inside the shared Redis channel
layer, so a message intended for webside user 42 cannot accidentally reach
a homonymous user on another tenant.
"""

from __future__ import annotations


def user_group(schema_name: str, user_id: int | str) -> str:
    """Return the per-user channel-layer group name for *schema_name*."""
    return f"tenant_{schema_name}_user_{user_id}"


def admins_group(schema_name: str) -> str:
    """Return the per-tenant admin channel-layer group name."""
    return f"tenant_{schema_name}_admins"
