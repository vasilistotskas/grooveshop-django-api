"""Tests for the OrderAdmin bulk status actions (G0245).

A mixed selection (some rows in an illegal transition state) must update the
eligible rows and report the rest — never 500 and roll back the whole batch
because one row raised InvalidStatusTransitionError.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite

from order.admin import OrderAdmin
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order


@pytest.mark.django_db
class TestOrderAdminBulkStatusActions:
    def _admin(self):
        return OrderAdmin(Order, AdminSite())

    def test_mark_as_processing_mixed_selection_updates_eligible_only(self):
        eligible = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        # COMPLETED → PROCESSING is an illegal transition; the service raises
        # InvalidStatusTransitionError (an OrderServiceError, not ValueError).
        ineligible = OrderFactory(
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
            num_order_items=0,
        )

        admin = self._admin()
        queryset = Order.objects.filter(pk__in=[eligible.pk, ineligible.pk])

        # Must not raise — the illegal row is caught and reported.
        with patch.object(admin, "message_user") as mock_message:
            admin.mark_as_processing(request=object(), queryset=queryset)

        eligible.refresh_from_db()
        ineligible.refresh_from_db()

        # The eligible row advanced and survived despite the failing sibling.
        assert eligible.status == OrderStatus.PROCESSING
        # The ineligible row is untouched (no batch-wide rollback).
        assert ineligible.status == OrderStatus.COMPLETED
        # Both rows produced a message (one success, one skipped).
        assert mock_message.call_count == 2
