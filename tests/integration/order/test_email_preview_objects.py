"""The object each order email is about, found from a real order for the
admin preview (``order.emails._PREVIEW_OBJECTS``)."""

from __future__ import annotations

import pytest

from order.emails import _PREVIEW_OBJECTS, NothingToPreview
from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db


def test_an_order_without_a_credit_receipt_has_nothing_to_preview():
    order = OrderFactory(num_order_items=0)

    with pytest.raises(NothingToPreview):
        _PREVIEW_OBJECTS["order.credit_issued"](order)
