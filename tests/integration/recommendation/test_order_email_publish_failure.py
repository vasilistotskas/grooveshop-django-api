"""The order e-mail's impression is not lost to a broker outage."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from kombu.exceptions import OperationalError

from order.factories.order import OrderFactory
from recommendation.engine import Suggestion
from recommendation.enum import EventKind, Surface
from recommendation.order_email import EmailStrip, record_impression
from recommendation.tasks import record_recommendation_event
from tenant.models import DeferredDispatch

pytestmark = pytest.mark.django_db


def test_a_failed_publish_keeps_the_impression_for_the_sweep():
    order = OrderFactory(num_order_items=0)
    strip = EmailStrip(
        impression_id=uuid.uuid4(),
        suggestions=[
            Suggestion(
                product_id=3, strategy="curated", relation_type=None, score=1.0
            )
        ],
        items=[],
    )

    with patch.object(
        record_recommendation_event,
        "apply_async",
        side_effect=OperationalError("broker unreachable"),
    ):
        record_impression(order, strip)

    row = DeferredDispatch.objects.get()
    assert row.task_name == record_recommendation_event.name
    assert row.kwargs["impression_id"] == str(strip.impression_id)
    assert row.kwargs["kind"] == EventKind.IMPRESSION
    assert row.kwargs["surface"] == Surface.ORDER_EMAIL
