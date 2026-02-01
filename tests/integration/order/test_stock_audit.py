import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from order.models.stock_log import StockLog
from order.stock import StockManager
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@pytest.mark.django_db(transaction=True)
class TestStockChangesAreAudited:
    """
    Test that all stock operations create comprehensive audit log entries
    with all required fields.
    """

    def setup_method(self):
        """Set up test data for each test method."""
        self.user = UserAccountFactory.create()

    @pytest.mark.parametrize(
        "operation_type,initial_stock,quantity,expected_delta,expected_stock_after",
        [
            # RESERVE operations - stock_after equals stock_before (no physical change)
            ("RESERVE", 100, 10, -10, 100),
            ("RESERVE", 50, 5, -5, 50),
            ("RESERVE", 20, 15, -15, 20),
            ("RESERVE", 1, 1, -1, 1),
            # RELEASE operations - stock_after equals stock_before (no physical change)
            ("RELEASE", 100, 10, 10, 100),
            ("RELEASE", 50, 5, 5, 50),
            ("RELEASE", 20, 15, 15, 20),
            # DECREMENT operations - stock_after = stock_before - quantity
            ("DECREMENT", 100, 10, -10, 90),
            ("DECREMENT", 50, 25, -25, 25),
            ("DECREMENT", 20, 20, -20, 0),
            ("DECREMENT", 15, 5, -5, 10),
            # INCREMENT operations - stock_after = stock_before + quantity
            ("INCREMENT", 100, 10, 10, 110),
            ("INCREMENT", 50, 25, 25, 75),
            ("INCREMENT", 0, 20, 20, 20),
            ("INCREMENT", 5, 5, 5, 10),
        ],
        ids=[
            # RESERVE test cases
            "reserve_10_from_100",
            "reserve_5_from_50",
            "reserve_15_from_20",
            "reserve_1_from_1",
            # RELEASE test cases
            "release_10_to_100",
            "release_5_to_50",
            "release_15_to_20",
            # DECREMENT test cases
            "decrement_10_from_100",
            "decrement_25_from_50",
            "decrement_all_20",
            "decrement_5_from_15",
            # INCREMENT test cases
            "increment_10_to_110",
            "increment_25_to_75",
            "increment_20_from_zero",
            "increment_5_to_10",
        ],
    )
    def test_stock_operation_creates_audit_log(
        self,
        operation_type,
        initial_stock,
        quantity,
        expected_delta,
        expected_stock_after,
    ):
        """
        Test that each stock operation type creates a StockLog entry with all required fields.

        For each operation type (RESERVE, RELEASE, DECREMENT, INCREMENT):
        1. Perform the operation
        2. Verify StockLog entry was created
        3. Verify all required fields are populated
        4. Verify stock_before, stock_after, and quantity_delta are correct
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=initial_stock
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Count existing logs before operation
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Perform the operation based on type
        if operation_type == "RESERVE":
            # Reserve stock
            session_id = f"test-session-{timezone.now().timestamp()}"
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=session_id,
                user_id=self.user.id,
            )

            # Verify reservation was created
            assert reservation is not None
            assert reservation.quantity == quantity

        elif operation_type == "RELEASE":
            # First create a reservation to release
            session_id = f"test-session-{timezone.now().timestamp()}"
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=session_id,
                user_id=self.user.id,
            )

            # Clear the log count after reservation (we're testing release)
            initial_log_count = StockLog.objects.filter(product=product).count()

            # Release the reservation
            StockManager.release_reservation(reservation.id)

        elif operation_type == "DECREMENT":
            # Direct stock decrement
            # Create a minimal order without items to avoid signal handlers
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

            StockManager.decrement_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="Test decrement operation",
            )

        elif operation_type == "INCREMENT":
            # Stock increment (e.g., order cancellation)
            # Create a minimal order without items to avoid signal handlers
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

            StockManager.increment_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="Test increment operation",
            )

        # Verify StockLog entry was created
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        assert logs.count() == initial_log_count + 1, (
            f"Expected {initial_log_count + 1} log entries after {operation_type}, "
            f"but found {logs.count()}"
        )

        # Get the most recent log entry
        log = logs.first()

        # Verify required field: operation_type
        assert log.operation_type is not None, (
            "operation_type should not be None"
        )
        assert log.operation_type == getattr(
            StockLog, f"OPERATION_{operation_type}"
        ), (
            f"operation_type should be {operation_type}, but got {log.operation_type}"
        )

        # Verify required field: quantity_delta
        assert log.quantity_delta is not None, (
            "quantity_delta should not be None"
        )
        assert log.quantity_delta == expected_delta, (
            f"quantity_delta should be {expected_delta}, but got {log.quantity_delta}"
        )

        # Verify required field: stock_before
        assert log.stock_before is not None, "stock_before should not be None"
        assert log.stock_before == initial_stock, (
            f"stock_before should be {initial_stock}, but got {log.stock_before}"
        )

        # Verify required field: stock_after
        assert log.stock_after is not None, "stock_after should not be None"
        assert log.stock_after == expected_stock_after, (
            f"stock_after should be {expected_stock_after}, but got {log.stock_after}"
        )

        # Verify required field: product
        assert log.product is not None, "product should not be None"
        assert log.product.id == product.id, (
            f"product_id should be {product.id}, but got {log.product.id}"
        )

        # Verify required field: reason
        assert log.reason is not None, "reason should not be None"
        assert len(log.reason) > 0, "reason should not be empty"

        # Verify required field: timestamp (via created_at from TimeStampMixinModel)
        assert log.created_at is not None, (
            "created_at timestamp should not be None"
        )

        # Verify timestamp is recent (within last 5 seconds)
        time_diff = (timezone.now() - log.created_at).total_seconds()
        assert time_diff < 5, (
            f"Log timestamp should be recent, but was {time_diff} seconds ago"
        )

        # Verify order_id field (may be None for RESERVE/RELEASE operations)
        if operation_type in ["DECREMENT", "INCREMENT"]:
            assert log.order is not None, (
                f"order should not be None for {operation_type} operations"
            )

        # Verify performed_by field (may be None for system operations)
        if operation_type == "RESERVE":
            assert log.performed_by is not None, (
                "performed_by should not be None for RESERVE operations with user"
            )
            assert log.performed_by.id == self.user.id

    @pytest.mark.parametrize(
        "num_operations,operation_type",
        [
            # Multiple RESERVE operations
            (3, "RESERVE"),
            (5, "RESERVE"),
            (10, "RESERVE"),
            # Multiple DECREMENT operations
            (3, "DECREMENT"),
            (5, "DECREMENT"),
            # Multiple INCREMENT operations
            (3, "INCREMENT"),
            (5, "INCREMENT"),
        ],
        ids=[
            "3_reserves",
            "5_reserves",
            "10_reserves",
            "3_decrements",
            "5_decrements",
            "3_increments",
            "5_increments",
        ],
    )
    def test_multiple_operations_create_multiple_logs(
        self, num_operations, operation_type
    ):
        """
        Test that multiple stock operations create multiple audit log entries.

        Verifies that each operation creates its own log entry and that
        logs are properly ordered by timestamp.
        """
        # Create product with sufficient stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            stock=1000,  # Large stock to accommodate multiple operations
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Count initial logs
        initial_log_count = StockLog.objects.filter(product=product).count()

        # Perform multiple operations
        for i in range(num_operations):
            if operation_type == "RESERVE":
                session_id = f"test-session-{i}-{timezone.now().timestamp()}"
                StockManager.reserve_stock(
                    product_id=product.id,
                    quantity=5,
                    session_id=session_id,
                    user_id=self.user.id,
                )
            elif operation_type == "DECREMENT":
                # Create minimal order without items to avoid signal handlers
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
                    payment_id=f"test_payment_{i}_{timezone.now().timestamp()}",
                    first_name="Test",
                    last_name="User",
                    email="test@example.com",
                    street="Test St",
                    street_number="123",
                    city="Test City",
                    zipcode="12345",
                    phone="+1234567890",
                )

                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=5,
                    order_id=order.id,
                    reason=f"Test decrement {i}",
                )
            elif operation_type == "INCREMENT":
                # Create minimal order without items to avoid signal handlers
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
                    payment_id=f"test_payment_{i}_{timezone.now().timestamp()}",
                    first_name="Test",
                    last_name="User",
                    email="test@example.com",
                    street="Test St",
                    street_number="123",
                    city="Test City",
                    zipcode="12345",
                    phone="+1234567890",
                )

                StockManager.increment_stock(
                    product_id=product.id,
                    quantity=5,
                    order_id=order.id,
                    reason=f"Test increment {i}",
                )

        # Verify correct number of logs created
        final_log_count = StockLog.objects.filter(product=product).count()
        expected_count = initial_log_count + num_operations
        assert final_log_count == expected_count, (
            f"Expected {expected_count} log entries after {num_operations} operations, "
            f"but found {final_log_count}"
        )

        # Verify logs are ordered by timestamp (most recent first)
        logs = StockLog.objects.filter(product=product).order_by("-created_at")
        previous_timestamp = None
        for log in logs:
            if previous_timestamp is not None:
                assert log.created_at <= previous_timestamp, (
                    "Logs should be ordered by created_at descending"
                )
            previous_timestamp = log.created_at

    @pytest.mark.parametrize(
        "stock_before,quantity,operation_type",
        [
            # RESERVE: stock_after should equal stock_before
            (100, 10, "RESERVE"),
            (50, 25, "RESERVE"),
            # RELEASE: stock_after should equal stock_before
            (100, 10, "RELEASE"),
            (50, 25, "RELEASE"),
            # DECREMENT: stock_after should be stock_before - quantity
            (100, 10, "DECREMENT"),
            (50, 25, "DECREMENT"),
            (20, 20, "DECREMENT"),
            # INCREMENT: stock_after should be stock_before + quantity
            (100, 10, "INCREMENT"),
            (50, 25, "INCREMENT"),
            (0, 20, "INCREMENT"),
        ],
        ids=[
            "reserve_stock_unchanged",
            "reserve_large_stock_unchanged",
            "release_stock_unchanged",
            "release_large_stock_unchanged",
            "decrement_reduces_stock",
            "decrement_reduces_stock_half",
            "decrement_to_zero",
            "increment_increases_stock",
            "increment_increases_stock_half",
            "increment_from_zero",
        ],
    )
    def test_stock_log_calculation_accuracy(
        self, stock_before, quantity, operation_type
    ):
        """
        Test that StockLog accurately records stock_before and stock_after values.

        Verifies that the relationship between stock_before, stock_after, and
        quantity_delta is mathematically correct for each operation type.
        """
        # Create product with initial stock
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=stock_before
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Perform operation
        if operation_type == "RESERVE":
            session_id = f"test-session-{timezone.now().timestamp()}"
            StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=session_id,
                user_id=self.user.id,
            )
            expected_stock_after = stock_before  # No physical change
            expected_delta = -quantity

        elif operation_type == "RELEASE":
            # First create a reservation
            session_id = f"test-session-{timezone.now().timestamp()}"
            reservation = StockManager.reserve_stock(
                product_id=product.id,
                quantity=quantity,
                session_id=session_id,
                user_id=self.user.id,
            )
            # Then release it
            StockManager.release_reservation(reservation.id)
            expected_stock_after = stock_before  # No physical change
            expected_delta = quantity

        elif operation_type == "DECREMENT":
            # Create minimal order without items to avoid signal handlers
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

            StockManager.decrement_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="Test decrement",
            )
            expected_stock_after = stock_before - quantity
            expected_delta = -quantity

        elif operation_type == "INCREMENT":
            # Create minimal order without items to avoid signal handlers
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

            StockManager.increment_stock(
                product_id=product.id,
                quantity=quantity,
                order_id=order.id,
                reason="Test increment",
            )
            expected_stock_after = stock_before + quantity
            expected_delta = quantity

        # Get the most recent log entry
        log = (
            StockLog.objects.filter(
                product=product,
                operation_type=getattr(StockLog, f"OPERATION_{operation_type}"),
            )
            .order_by("-created_at")
            .first()
        )

        assert log is not None, f"No log found for {operation_type} operation"

        # Verify stock_before is correct
        assert log.stock_before == stock_before, (
            f"stock_before should be {stock_before}, but got {log.stock_before}"
        )

        # Verify stock_after is correct
        assert log.stock_after == expected_stock_after, (
            f"stock_after should be {expected_stock_after}, but got {log.stock_after}"
        )

        # Verify quantity_delta is correct
        assert log.quantity_delta == expected_delta, (
            f"quantity_delta should be {expected_delta}, but got {log.quantity_delta}"
        )

        # Verify mathematical relationship: stock_after = stock_before + quantity_delta
        # EXCEPT for RESERVE and RELEASE where stock_after = stock_before (no physical change)
        if operation_type in ["RESERVE", "RELEASE"]:
            # For RESERVE and RELEASE, stock doesn't physically change
            assert log.stock_after == log.stock_before, (
                f"For {operation_type}, stock_after ({log.stock_after}) should equal "
                f"stock_before ({log.stock_before}) since no physical stock change occurs"
            )
        else:
            # For DECREMENT and INCREMENT, verify mathematical relationship
            calculated_stock_after = log.stock_before + log.quantity_delta
            assert log.stock_after == calculated_stock_after, (
                f"stock_after ({log.stock_after}) should equal "
                f"stock_before ({log.stock_before}) + quantity_delta ({log.quantity_delta}) "
                f"= {calculated_stock_after}"
            )

        # Verify actual product stock matches expected (for DECREMENT/INCREMENT)
        if operation_type in ["DECREMENT", "INCREMENT"]:
            product.refresh_from_db()
            assert product.stock == expected_stock_after, (
                f"Product stock should be {expected_stock_after}, but got {product.stock}"
            )

    def test_audit_log_contains_all_required_fields(self):
        """
        Test that StockLog entries contain all required fields for audit purposes.

        Verifies that every field required for a complete audit trail is present
        and properly populated in the StockLog model.
        """
        # Create product
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Create order for decrement operation
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

        # Perform a decrement operation (most complete log entry)
        StockManager.decrement_stock(
            product_id=product.id,
            quantity=10,
            order_id=order.id,
            reason="Complete audit test",
        )

        # Get the log entry
        log = StockLog.objects.filter(
            product=product, operation_type=StockLog.OPERATION_DECREMENT
        ).first()

        assert log is not None, "StockLog entry should exist"

        # Verify all required fields are present and not None
        required_fields = {
            "id": log.id,
            "product": log.product,
            "operation_type": log.operation_type,
            "quantity_delta": log.quantity_delta,
            "stock_before": log.stock_before,
            "stock_after": log.stock_after,
            "reason": log.reason,
            "created_at": log.created_at,
            "updated_at": log.updated_at,
        }

        for field_name, field_value in required_fields.items():
            assert field_value is not None, (
                f"Required field '{field_name}' should not be None"
            )

        # Verify optional fields that should be present for this operation
        assert log.order is not None, (
            "order should be present for DECREMENT operation"
        )
        assert log.order.id == order.id, "order should match the provided order"

        # Verify field types and constraints
        assert isinstance(log.id, int), "id should be an integer"
        assert isinstance(log.quantity_delta, int), (
            "quantity_delta should be an integer"
        )
        assert isinstance(log.stock_before, int), (
            "stock_before should be an integer"
        )
        assert isinstance(log.stock_after, int), (
            "stock_after should be an integer"
        )
        assert isinstance(log.reason, str), "reason should be a string"
        assert len(log.reason) > 0, "reason should not be empty"
        assert log.operation_type in [
            StockLog.OPERATION_RESERVE,
            StockLog.OPERATION_RELEASE,
            StockLog.OPERATION_DECREMENT,
            StockLog.OPERATION_INCREMENT,
        ], "operation_type should be one of the valid choices"

    def test_audit_log_persists_across_operations(self):
        """
        Test that audit logs persist and accumulate across multiple operations.

        Verifies that audit logs are permanent records that accumulate over time
        and are not deleted or modified by subsequent operations.
        """
        # Create product
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=100
        )
        product.set_current_language("en")
        product.name = "Test Product"
        product.save()

        # Perform a sequence of operations
        from order.models import Order
        from order.enum.status import OrderStatus, PaymentStatus
        from pay_way.factories import PayWayFactory
        from country.factories import CountryFactory

        pay_way = PayWayFactory.create()
        country = CountryFactory.create()

        # Operation 1: Reserve
        session_id = f"test-session-{timezone.now().timestamp()}"
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id=session_id,
            user_id=self.user.id,
        )

        # Verify 1 log entry
        assert StockLog.objects.filter(product=product).count() == 1

        # Operation 2: Release
        StockManager.release_reservation(reservation.id)

        # Verify 2 log entries (reserve + release)
        assert StockLog.objects.filter(product=product).count() == 2

        # Operation 3: Decrement
        order1 = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_1_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        StockManager.decrement_stock(
            product_id=product.id,
            quantity=20,
            order_id=order1.id,
            reason="First decrement",
        )

        # Verify 3 log entries
        assert StockLog.objects.filter(product=product).count() == 3

        # Operation 4: Increment
        order2 = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_2_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        StockManager.increment_stock(
            product_id=product.id,
            quantity=10,
            order_id=order2.id,
            reason="First increment",
        )

        # Verify 4 log entries
        assert StockLog.objects.filter(product=product).count() == 4

        # Operation 5: Another decrement
        order3 = Order.objects.create(
            user=self.user,
            pay_way=pay_way,
            country=country,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id=f"test_payment_3_{timezone.now().timestamp()}",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            street="Test St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            phone="+1234567890",
        )

        StockManager.decrement_stock(
            product_id=product.id,
            quantity=5,
            order_id=order3.id,
            reason="Second decrement",
        )

        # Verify 5 log entries - all operations logged
        final_logs = StockLog.objects.filter(product=product).order_by(
            "created_at"
        )
        assert final_logs.count() == 5

        # Verify each log entry is distinct and unchanged
        operation_types = [log.operation_type for log in final_logs]
        assert operation_types == [
            StockLog.OPERATION_RESERVE,
            StockLog.OPERATION_RELEASE,
            StockLog.OPERATION_DECREMENT,
            StockLog.OPERATION_INCREMENT,
            StockLog.OPERATION_DECREMENT,
        ]

        # Verify logs are immutable (created_at == updated_at for all)
        for log in final_logs:
            time_diff = abs((log.updated_at - log.created_at).total_seconds())
            assert time_diff < 1, (
                "Logs should not be modified after creation "
                "(updated_at should equal created_at)"
            )
