from __future__ import annotations

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from agent.outbox import record_order_created
from order.enum.attribution import OrderSourceType
from order.models.attribution import OrderAttribution


@receiver(
    post_save,
    sender=OrderAttribution,
    dispatch_uid="agent.record_agent_order_created",
)
def record_agent_order_created(
    sender: type[OrderAttribution],
    instance: OrderAttribution,
    created: bool,
    **kwargs: Any,
) -> None:
    """An order's attribution is written once, in its creation
    transaction, and is what says an agent placed it: its "Order
    created" event is recorded with it."""
    if created and instance.source_type == OrderSourceType.AGENT:
        record_order_created(instance.order_id)
