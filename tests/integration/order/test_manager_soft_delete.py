"""Tests for Order soft-delete manager/queryset contract (G0246).

Bulk queryset deletes must SOFT-delete (mirroring the per-instance
SoftDeleteModel.delete()), and soft-deleted orders must stay reachable via
the manager's all_with_deleted() / deleted_only() accessors.
"""

from __future__ import annotations

import pytest

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.order import Order


@pytest.mark.django_db
class TestOrderSoftDeleteManager:
    def test_bulk_delete_soft_deletes(self):
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)

        result = Order.objects.filter(pk=order.pk).delete()

        # Absent from the default (exclude_deleted) manager...
        assert not Order.objects.filter(pk=order.pk).exists()
        # ...but the row still exists in the DB, flagged deleted (not hard-
        # deleted) and reachable via the soft-delete accessors.
        assert Order.objects.all_with_deleted().filter(pk=order.pk).exists()
        assert Order.objects.deleted_only().filter(pk=order.pk).exists()
        # Django-style (count, per-label) return shape.
        assert result[0] == 1

    def test_restore_undeletes(self):
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
        Order.objects.filter(pk=order.pk).delete()

        Order.objects.deleted_only().filter(pk=order.pk).restore()

        assert Order.objects.filter(pk=order.pk).exists()
        assert not Order.objects.deleted_only().filter(pk=order.pk).exists()

    def test_hard_delete_removes_row(self):
        order = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)

        Order.objects.filter(pk=order.pk).hard_delete()

        assert not Order.objects.all_with_deleted().filter(pk=order.pk).exists()
