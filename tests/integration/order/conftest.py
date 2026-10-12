from __future__ import annotations

import pytest

from order.signals import order_refunded


@pytest.fixture
def refunded_signals():
    """Every ``order_refunded`` the test caused, as its keyword arguments."""
    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_refunded.connect(receiver, weak=False)
    yield received
    order_refunded.disconnect(receiver)
