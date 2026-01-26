import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from order.factories import OrderFactory
from order.models.order import Order


@pytest.mark.django_db
class TestStockReservationIdsField:
    """Test the stock_reservation_ids JSONField on Order model."""

    def test_stock_reservation_ids_default_empty_list(self):
        """Test that stock_reservation_ids defaults to an empty list."""
        order = OrderFactory()

        assert order.stock_reservation_ids == []
        assert isinstance(order.stock_reservation_ids, list)

    def test_stock_reservation_ids_can_store_ids(self):
        """Test that stock_reservation_ids can store reservation IDs."""
        reservation_ids = [1, 2, 3, 4, 5]
        order = OrderFactory(stock_reservation_ids=reservation_ids)

        assert order.stock_reservation_ids == reservation_ids

    def test_stock_reservation_ids_persists_to_database(self):
        """Test that stock_reservation_ids is saved and retrieved correctly."""
        reservation_ids = [10, 20, 30]
        order = OrderFactory(stock_reservation_ids=reservation_ids)
        order_id = order.id

        # Clear the instance from memory
        del order

        # Retrieve from database
        retrieved_order = Order.objects.get(id=order_id)

        assert retrieved_order.stock_reservation_ids == reservation_ids

    def test_stock_reservation_ids_can_be_updated(self):
        """Test that stock_reservation_ids can be updated after creation."""
        order = OrderFactory(stock_reservation_ids=[])

        # Update with new reservation IDs
        new_ids = [100, 200, 300]
        order.stock_reservation_ids = new_ids
        order.save()

        # Verify update persisted
        order.refresh_from_db()
        assert order.stock_reservation_ids == new_ids

    def test_stock_reservation_ids_can_be_empty(self):
        """Test that stock_reservation_ids can be explicitly set to empty list."""
        order = OrderFactory(stock_reservation_ids=[1, 2, 3])

        # Clear the list
        order.stock_reservation_ids = []
        order.save()

        order.refresh_from_db()
        assert order.stock_reservation_ids == []

    def test_stock_reservation_ids_supports_large_lists(self):
        """Test that stock_reservation_ids can handle large lists of IDs."""
        # Create a large list of reservation IDs
        large_list = list(range(1, 101))  # 100 IDs

        order = OrderFactory(stock_reservation_ids=large_list)
        order.refresh_from_db()

        assert order.stock_reservation_ids == large_list
        assert len(order.stock_reservation_ids) == 100

    def test_stock_reservation_ids_field_metadata(self):
        """Test that the field has correct metadata and help text."""
        field = Order._meta.get_field("stock_reservation_ids")

        assert field.blank is True
        assert field.default is list
        assert "audit trail" in field.help_text.lower()
        assert "reservation" in field.help_text.lower()

    def test_order_with_stock_reservation_ids_in_queryset(self):
        """Test that stock_reservation_ids is included in queryset results."""
        reservation_ids = [7, 8, 9]
        order = OrderFactory(stock_reservation_ids=reservation_ids)

        # Fetch via queryset
        fetched_order = Order.objects.filter(id=order.id).first()

        assert fetched_order.stock_reservation_ids == reservation_ids

    def test_stock_reservation_ids_no_extra_queries(self):
        """Test that accessing stock_reservation_ids doesn't cause extra queries."""
        order = OrderFactory(stock_reservation_ids=[1, 2, 3])

        # Clear any cached queries
        with CaptureQueriesContext(connection) as context:
            # Access the field
            _ = order.stock_reservation_ids

            # Should not cause any additional queries
            assert len(context.captured_queries) == 0
