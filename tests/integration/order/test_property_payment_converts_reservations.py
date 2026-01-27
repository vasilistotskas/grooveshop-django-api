from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from order.models.stock_log import StockLog
from order.models.stock_reservation import StockReservation
from order.stock import StockManager
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db(transaction=True)
class TestProperty7PaymentConfirmationConvertsReservations:
    """
    Test that payment confirmation properly converts stock reservations to
    permanent stock decrements with all required side effects.
    """

    def setup_method(self):
        """Set up test data for each test method."""
        self.user = UserAccountFactory.create()
        # Patch the TTL to ensure reservations don't expire during slow test executions
        from unittest.mock import patch
        self.patcher = patch("order.stock.StockManager.get_reservation_ttl_minutes", return_value=60)
        self.patcher.start()

    def teardown_method(self):
        self.patcher.stop()

    @pytest.mark.parametrize(
        "scenario,products_data,expected_results",
        [
            # Single product, single reservation
            (
                "single_product_single_reservation",
                [{"initial_stock": 100, "reserved_qty": 10}],
                {
                    "total_reservations": 1,
                    "expected_stock_after": [90],  # 100 - 10
                    "expected_consumed": [True],
                },
            ),
            # Single product, small quantity
            (
                "single_product_small_quantity",
                [{"initial_stock": 50, "reserved_qty": 1}],
                {
                    "total_reservations": 1,
                    "expected_stock_after": [49],  # 50 - 1
                    "expected_consumed": [True],
                },
            ),
            # Single product, large quantity
            (
                "single_product_large_quantity",
                [{"initial_stock": 200, "reserved_qty": 150}],
                {
                    "total_reservations": 1,
                    "expected_stock_after": [50],  # 200 - 150
                    "expected_consumed": [True],
                },
            ),
            # Single product, full stock reservation
            (
                "single_product_full_stock",
                [{"initial_stock": 25, "reserved_qty": 25}],
                {
                    "total_reservations": 1,
                    "expected_stock_after": [0],  # 25 - 25
                    "expected_consumed": [True],
                },
            ),
            # Multiple products, single reservation each
            (
                "multiple_products_single_reservations",
                [
                    {"initial_stock": 100, "reserved_qty": 10},
                    {"initial_stock": 50, "reserved_qty": 5},
                    {"initial_stock": 75, "reserved_qty": 15},
                ],
                {
                    "total_reservations": 3,
                    "expected_stock_after": [90, 45, 60],  # 100-10, 50-5, 75-15
                    "expected_consumed": [True, True, True],
                },
            ),
            # Multiple products, varying quantities
            (
                "multiple_products_varying_quantities",
                [
                    {"initial_stock": 200, "reserved_qty": 50},
                    {"initial_stock": 100, "reserved_qty": 100},
                    {"initial_stock": 30, "reserved_qty": 1},
                    {"initial_stock": 500, "reserved_qty": 250},
                ],
                {
                    "total_reservations": 4,
                    "expected_stock_after": [
                        150,
                        0,
                        29,
                        250,
                    ],  # Various decrements
                    "expected_consumed": [True, True, True, True],
                },
            ),
            # Edge case: Low stock with reservation
            (
                "low_stock_reservation",
                [{"initial_stock": 3, "reserved_qty": 2}],
                {
                    "total_reservations": 1,
                    "expected_stock_after": [1],  # 3 - 2
                    "expected_consumed": [True],
                },
            ),
            # Edge case: Multiple products with mixed stock levels
            (
                "mixed_stock_levels",
                [
                    {"initial_stock": 1, "reserved_qty": 1},
                    {"initial_stock": 1000, "reserved_qty": 999},
                    {"initial_stock": 10, "reserved_qty": 5},
                ],
                {
                    "total_reservations": 3,
                    "expected_stock_after": [0, 1, 5],  # 1-1, 1000-999, 10-5
                    "expected_consumed": [True, True, True],
                },
            ),
        ],
        ids=[
            "single_product_single_reservation",
            "single_product_small_quantity",
            "single_product_large_quantity",
            "single_product_full_stock",
            "multiple_products_single_reservations",
            "multiple_products_varying_quantities",
            "low_stock_reservation",
            "mixed_stock_levels",
        ],
    )
    def test_convert_reservation_to_sale_marks_consumed_and_decrements_stock(
        self, scenario, products_data, expected_results
    ):
        """
        Test that convert_reservation_to_sale properly converts reservations.

        For each scenario:
        1. Create products with initial stock
        2. Create reservations for each product
        3. Convert reservations to sales
        4. Verify reservations are marked as consumed
        5. Verify stock is decremented correctly
        6. Verify audit logs are created
        """
        # Create products and reservations based on test data
        products = []
        reservations = []
        session_id = f"test-session-{timezone.now().timestamp()}"

        for product_data in products_data:
            # Create product with initial stock
            product = ProductFactory.create(
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                stock=product_data["initial_stock"],
            )
            product.set_current_language("en")
            product.name = f"Test Product {len(products) + 1}"
            product.save()
            products.append(product)

            # Create reservation for this product
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=product_data["reserved_qty"],
                session_id=session_id,
                user_id=self.user.id,
            )
            reservations.append(reservation)

        # Verify reservations were created correctly
        assert len(reservations) == expected_results["total_reservations"]
        for reservation in reservations:
            assert reservation.consumed is False
            assert reservation.order is None

        # Create a minimal order for conversion
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        order = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        # Count stock logs before conversion
        initial_log_counts = [
            StockLog.objects.filter(product=product).count()
            for product in products
        ]

        # Convert each reservation to sale
        for reservation in reservations:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        # Verify: Reservations are marked as consumed
        for idx, reservation in enumerate(reservations):
            reservation.refresh_from_db()
            assert (
                reservation.consumed
                is expected_results["expected_consumed"][idx]
            ), (
                f"Reservation {idx} should be consumed={expected_results['expected_consumed'][idx]}, "
                f"but got consumed={reservation.consumed}"
            )
            assert reservation.order == order, (
                f"Reservation {idx} should be linked to order {order.id}, "
                f"but got order={reservation.order}"
            )

        # Verify: Stock is decremented correctly
        for idx, product in enumerate(products):
            product.refresh_from_db()
            expected_stock = expected_results["expected_stock_after"][idx]
            assert product.stock == expected_stock, (
                f"Product {idx} stock should be {expected_stock}, "
                f"but got {product.stock}"
            )

        # Verify: Audit logs are created for each conversion
        for idx, product in enumerate(products):
            logs = StockLog.objects.filter(product=product).order_by(
                "-created_at"
            )
            # Should have one more log than before (the DECREMENT from conversion)
            # Note: We already have RESERVE logs from reserve_stock
            assert logs.count() > initial_log_counts[idx], (
                f"Product {idx} should have additional log entries after conversion"
            )

            # Find the DECREMENT log from conversion
            decrement_logs = logs.filter(
                operation_type=StockLog.OPERATION_DECREMENT
            )
            assert decrement_logs.exists(), (
                f"Product {idx} should have a DECREMENT log from conversion"
            )

            # Verify the most recent DECREMENT log
            decrement_log = decrement_logs.first()
            assert decrement_log.order == order, (
                f"DECREMENT log should be linked to order {order.id}"
            )
            assert (
                decrement_log.quantity_delta
                == -products_data[idx]["reserved_qty"]
            ), (
                f"DECREMENT log should have quantity_delta=-{products_data[idx]['reserved_qty']}"
            )
            assert (
                decrement_log.stock_after
                == expected_results["expected_stock_after"][idx]
            ), (
                f"DECREMENT log should have stock_after={expected_results['expected_stock_after'][idx]}"
            )

    def test_convert_reservation_to_sale_with_expired_reservation_fails(self):
        """
        Test that converting an expired reservation raises an error.

        Expired reservations should not be convertible to sales.
        """
        # Create product with stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create reservation that's already expired
        session_id = f"test-session-{timezone.now().timestamp()}"
        expired_time = timezone.now() - timedelta(minutes=30)  # 30 minutes ago

        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id=session_id,
            reserved_by=self.user,
            expires_at=expired_time,  # Already expired
            consumed=False,
        )

        # Create order
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        order = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        # Attempt to convert expired reservation should fail
        from order.exceptions import StockReservationError

        with pytest.raises(StockReservationError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        assert "expired" in str(exc_info.value).lower()

        # Verify reservation was not consumed
        reservation.refresh_from_db()
        assert reservation.consumed is False

        # Verify stock was not decremented
        product.refresh_from_db()
        assert product.stock == 100

    def test_convert_reservation_to_sale_with_already_consumed_reservation_fails(
        self,
    ):
        """
        Test that converting an already consumed reservation raises an error.

        Already consumed reservations should not be convertible again.
        """
        # Create product with stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create and immediately consume a reservation
        session_id = f"test-session-{timezone.now().timestamp()}"

        reservation = StockReservation.objects.create(
            product=product,
            quantity=10,
            session_id=session_id,
            reserved_by=self.user,
            expires_at=timezone.now() + timedelta(minutes=15),
            consumed=True,  # Already consumed
        )

        # Create order
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        order = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        # Attempt to convert already consumed reservation should fail
        from order.exceptions import StockReservationError

        with pytest.raises(StockReservationError) as exc_info:
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        assert "already consumed" in str(exc_info.value).lower()

        # Verify stock was not decremented (should still be 100)
        product.refresh_from_db()
        assert product.stock == 100

    def test_convert_reservation_to_sale_with_insufficient_stock_fails(self):
        """
        Test that conversion fails if stock was manually reduced below reservation.

        If stock was manually adjusted and is now insufficient, conversion should fail.
        """
        # Create product with stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create reservation
        session_id = f"test-session-{timezone.now().timestamp()}"
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=50,
            session_id=session_id,
            user_id=self.user.id,
        )

        # Manually reduce stock below reservation quantity (simulating admin adjustment)
        product.stock = 30  # Less than reserved 50
        product.save()

        # Create order
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        order = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        # Attempt to convert should fail due to insufficient stock
        from order.exceptions import InsufficientStockError

        with pytest.raises(InsufficientStockError):
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

        # Verify reservation was not consumed
        reservation.refresh_from_db()
        assert reservation.consumed is False

        # Verify stock was not decremented further
        product.refresh_from_db()
        assert product.stock == 30

    @pytest.mark.parametrize(
        "num_reservations,quantities",
        [
            (2, [10, 20]),
            (3, [5, 10, 15]),
            (5, [1, 2, 3, 4, 5]),
            (10, [1] * 10),
        ],
        ids=[
            "two_reservations",
            "three_reservations",
            "five_reservations",
            "ten_small_reservations",
        ],
    )
    def test_convert_multiple_reservations_for_same_product(
        self, num_reservations, quantities
    ):
        """
        Test converting multiple reservations for the same product.

        A single product can have multiple reservations (from different sessions)
        that all get converted when their respective orders are confirmed.
        """
        # Create product with sufficient stock
        total_quantity = sum(quantities)
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            stock=total_quantity + 100,  # Extra buffer
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        initial_stock = product.stock

        # Create multiple reservations with different session IDs
        reservations = []
        for idx, quantity in enumerate(quantities):
            session_id = f"test-session-{idx}-{timezone.now().timestamp()}"
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=session_id,
                user_id=self.user.id,
            )
            reservations.append(reservation)

        # Create orders and convert each reservation
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        for idx, reservation in enumerate(reservations):
            order = Order.objects.create(
                user=self.user,
                pay_way=pay_way,
                country=country,
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                payment_id=f"test_payment_{idx}_{timezone.now().timestamp()}",
                first_name="Test",
                last_name="User",
                email="test@example.com",
                street="Test St",
                street_number="123",
                city="Test City",
                zipcode="12345",
                phone="+1234567890",
            )

            # Convert reservation to sale
            StockManager.convert_reservation_to_sale(
                reservation_id=reservation.id, order_id=order.id
            )

            # Verify reservation is consumed
            reservation.refresh_from_db()
            assert reservation.consumed is True
            assert reservation.order == order

        # Verify total stock decrement equals sum of all quantities
        product.refresh_from_db()
        expected_stock = initial_stock - total_quantity
        assert product.stock == expected_stock, (
            f"Stock should be {expected_stock} after converting {num_reservations} reservations, "
            f"but got {product.stock}"
        )

        # Verify all reservations are consumed
        for reservation in reservations:
            reservation.refresh_from_db()
            assert reservation.consumed is True

    def test_convert_reservation_creates_correct_audit_log_fields(self):
        """
        Test that conversion creates audit log with all required fields.

        The conversion should create a DECREMENT log with:
        - Correct operation_type
        - Correct quantity_delta (negative)
        - Correct stock_before and stock_after
        - Link to order
        - Link to product
        - Timestamp
        - Reason mentioning reservation conversion
        """
        # Create product with stock
        initial_stock = 100
        reserved_qty = 25

        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create reservation
        session_id = f"test-session-{timezone.now().timestamp()}"
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=reserved_qty,
            session_id=session_id,
            user_id=self.user.id,
        )

        # Create order
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        order = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        # Count logs before conversion
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Convert reservation
        StockManager.convert_reservation_to_sale(
            reservation_id=reservation.id, order_id=order.id
        )

        # Verify new log was created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1

        # Get the DECREMENT log (most recent)
        decrement_log = logs.filter(
            operation_type=StockLog.OPERATION_DECREMENT
        ).first()
        assert decrement_log is not None

        # Verify all required fields
        assert decrement_log.operation_type == StockLog.OPERATION_DECREMENT
        assert decrement_log.quantity_delta == -reserved_qty
        assert decrement_log.stock_before == initial_stock
        assert decrement_log.stock_after == initial_stock - reserved_qty
        assert decrement_log.product == product
        assert decrement_log.order == order
        assert decrement_log.performed_by == self.user

        # Verify reason mentions reservation conversion
        assert "reservation" in decrement_log.reason.lower()
        assert "converted" in decrement_log.reason.lower()
        assert str(reservation.id) in decrement_log.reason
        assert str(order.id) in decrement_log.reason

        # Verify timestamp is recent
        time_diff = (timezone.now() - decrement_log.created_at).total_seconds()
        assert time_diff < 5, "Log timestamp should be recent"
