"""
Unit tests for StockReservation model.

Feature: checkout-order-audit
Tests basic model functionality, properties, and database constraints.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from order.models import StockReservation
from product.factories import ProductFactory
from user.factories import UserAccountFactory


@pytest.mark.django_db
class TestStockReservationModel:
    """Test StockReservation model basic functionality."""

    def test_create_stock_reservation(self):
        """Test creating a stock reservation with all required fields."""
        product = ProductFactory()
        user = UserAccountFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=5,
            reserved_by=user,
            session_id="test-session-123",
            expires_at=expires_at,
        )

        assert reservation.id is not None
        assert reservation.product == product
        assert reservation.quantity == 5
        assert reservation.reserved_by == user
        assert reservation.session_id == "test-session-123"
        assert reservation.expires_at == expires_at
        assert reservation.consumed is False
        assert reservation.order is None

    def test_create_guest_reservation(self):
        """Test creating a stock reservation for guest user (no reserved_by)."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=3,
            reserved_by=None,
            session_id="guest-session-456",
            expires_at=expires_at,
        )

        assert reservation.id is not None
        assert reservation.reserved_by is None
        assert reservation.session_id == "guest-session-456"

    def test_str_representation(self):
        """Test string representation of stock reservation."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="test-session",
            expires_at=expires_at,
        )

        str_repr = str(reservation)
        assert "Reservation" in str_repr
        assert str(reservation.id) in str_repr
        assert str(reservation.quantity) in str_repr
        assert "active" in str_repr

    def test_str_representation_consumed(self):
        """Test string representation of consumed reservation."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="test-session",
            expires_at=expires_at,
            consumed=True,
        )

        str_repr = str(reservation)
        assert "consumed" in str_repr

    def test_is_expired_property_false(self):
        """Test is_expired property returns False for active reservation."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
        )

        assert reservation.is_expired is False

    def test_is_expired_property_true(self):
        """Test is_expired property returns True for expired reservation."""
        product = ProductFactory()
        expires_at = timezone.now() - timedelta(minutes=1)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
        )

        assert reservation.is_expired is True

    def test_is_active_property_true(self):
        """Test is_active property returns True for active, non-expired reservation."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
            consumed=False,
        )

        assert reservation.is_active is True

    def test_is_active_property_false_consumed(self):
        """Test is_active property returns False for consumed reservation."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
            consumed=True,
        )

        assert reservation.is_active is False

    def test_is_active_property_false_expired(self):
        """Test is_active property returns False for expired reservation."""
        product = ProductFactory()
        expires_at = timezone.now() - timedelta(minutes=1)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
            consumed=False,
        )

        assert reservation.is_active is False

    def test_timestamps_auto_created(self):
        """Test that created_at and updated_at are automatically set."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
        )

        assert reservation.created_at is not None
        assert reservation.updated_at is not None
        assert reservation.created_at <= reservation.updated_at

    def test_product_cascade_delete(self):
        """Test that reservation references product correctly with soft delete."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
        )

        reservation_id = reservation.id

        # Product uses soft delete, so reservation should still exist and reference product
        product.delete()

        # Reservation should still exist and reference the soft-deleted product
        assert StockReservation.objects.filter(id=reservation_id).exists()
        reservation.refresh_from_db()
        assert reservation.product == product

    def test_user_set_null_on_delete(self):
        """Test that deleting user sets reserved_by to NULL."""
        product = ProductFactory()
        user = UserAccountFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            reserved_by=user,
            session_id="test-session",
            expires_at=expires_at,
        )

        user.delete()
        reservation.refresh_from_db()

        assert reservation.reserved_by is None

    def test_order_set_null_on_delete(self):
        """Test that reservation references order correctly with soft delete."""
        from order.factories import OrderFactory

        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)
        order = OrderFactory()

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="test-session",
            expires_at=expires_at,
            consumed=True,
            order=order,
        )

        # Order uses soft delete, so reservation should still reference it
        order.delete()
        reservation.refresh_from_db()

        # Reservation should still have order reference since it's soft-deleted
        assert reservation.order == order

    def test_ordering_by_created_at_desc(self):
        """Test that reservations are ordered by created_at descending."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        # Create reservations with slight time differences
        reservation1 = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="session-1",
            expires_at=expires_at,
        )

        reservation2 = StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="session-2",
            expires_at=expires_at,
        )

        reservations = list(StockReservation.objects.all())
        assert reservations[0].id == reservation2.id
        assert reservations[1].id == reservation1.id

    def test_multiple_reservations_same_product(self):
        """Test creating multiple reservations for the same product."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        reservation1 = StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="session-1",
            expires_at=expires_at,
        )

        reservation2 = StockReservation.objects.create(
            product=product,
            quantity=3,
            session_id="session-2",
            expires_at=expires_at,
        )

        assert StockReservation.objects.filter(product=product).count() == 2
        assert reservation1.id != reservation2.id

    def test_query_by_session_id(self):
        """Test querying reservations by session_id."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)
        session_id = "unique-session-789"

        StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id=session_id,
            expires_at=expires_at,
        )

        StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="other-session",
            expires_at=expires_at,
        )

        reservations = StockReservation.objects.filter(session_id=session_id)
        assert reservations.count() == 1
        assert reservations.first().session_id == session_id

    def test_query_by_consumed_status(self):
        """Test querying reservations by consumed status."""
        product = ProductFactory()
        expires_at = timezone.now() + timedelta(minutes=15)

        StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="session-1",
            expires_at=expires_at,
            consumed=False,
        )

        StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="session-2",
            expires_at=expires_at,
            consumed=True,
        )

        active_reservations = StockReservation.objects.filter(consumed=False)
        consumed_reservations = StockReservation.objects.filter(consumed=True)

        assert active_reservations.count() == 1
        assert consumed_reservations.count() == 1

    def test_query_expired_reservations(self):
        """Test querying expired reservations."""
        product = ProductFactory()
        now = timezone.now()

        # Create expired reservation
        StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="expired-session",
            expires_at=now - timedelta(minutes=5),
        )

        # Create active reservation
        StockReservation.objects.create(
            product=product,
            quantity=2,
            session_id="active-session",
            expires_at=now + timedelta(minutes=15),
        )

        expired = StockReservation.objects.filter(
            expires_at__lt=now, consumed=False
        )
        assert expired.count() == 1
        assert expired.first().session_id == "expired-session"
