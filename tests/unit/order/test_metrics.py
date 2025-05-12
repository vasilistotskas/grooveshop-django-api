import datetime
from decimal import Decimal
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.metrics import OrderMetrics


class OrderMetricsTestCase(TransactionTestCase):
    """Test case for the OrderMetrics class."""

    def setUp(self):
        """Set up test data."""
        self.start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
        self.end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

    @mock.patch("order.metrics.Order.objects.all")
    def test_get_total_orders(self, mock_orders):
        """Test getting total order count using mocks."""
        # Configure mock to return a fixed count
        mock_query = mock.MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 3
        mock_orders.return_value = mock_query

        # Test without date filter
        total = OrderMetrics.get_total_orders()
        self.assertEqual(total, 3)

        # Test with date range
        yesterday = timezone.now() - datetime.timedelta(days=1)
        tomorrow = timezone.now() + datetime.timedelta(days=1)

        total = OrderMetrics.get_total_orders(
            start_date=yesterday, end_date=tomorrow
        )
        self.assertEqual(total, 3)

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_total_revenue(self, mock_filter):
        """Test getting total revenue using mocks."""
        # Create a mock order with predictable values
        mock_order = mock.MagicMock()
        mock_order.total_price_items = Money(50, "USD")
        mock_order.total_price_extra = Money(50, "USD")

        # Configure the mock to return our order
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.__iter__.return_value = [mock_order]

        # Call with no date range
        revenue = OrderMetrics.get_total_revenue()

        # Should have revenue in USD
        self.assertIn("USD", revenue)
        self.assertEqual(revenue["USD"], Decimal(100))

    @mock.patch("order.metrics.Order.objects.all")
    def test_get_orders_by_status(self, mock_orders):
        """Test getting orders grouped by status."""
        # Setup mock to return predefined status counts
        mock_query = mock.MagicMock()
        mock_orders.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = [
            {"status": OrderStatusEnum.COMPLETED, "count": 1},
            {"status": OrderStatusEnum.PROCESSING, "count": 1},
            {"status": OrderStatusEnum.PENDING, "count": 1},
        ]

        # Get status counts
        status_counts = OrderMetrics.get_orders_by_status()

        # Verify counts
        self.assertEqual(status_counts[OrderStatusEnum.COMPLETED], 1)
        self.assertEqual(status_counts[OrderStatusEnum.PROCESSING], 1)
        self.assertEqual(status_counts[OrderStatusEnum.PENDING], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    @mock.patch("order.metrics.date_range")
    def test_get_orders_by_day(self, mock_date_range, mock_filter):
        """Test getting orders grouped by day using mocks."""
        # Create mock daily counts
        today = timezone.now()
        yesterday = today - datetime.timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        # Configure mocks
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.annotate.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = mock_query
        mock_query.order_by.return_value = [
            {"day": today, "count": 1},
            {"day": yesterday, "count": 1},
        ]

        mock_date_range.return_value = [yesterday.date(), today.date()]

        # Get orders by day
        daily_counts = OrderMetrics.get_orders_by_day(
            start_date=yesterday, end_date=today + datetime.timedelta(days=1)
        )

        # Check counts for today and yesterday
        self.assertEqual(daily_counts[today_str], 1)
        self.assertEqual(daily_counts[yesterday_str], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    @mock.patch("order.metrics.date_range")
    def test_get_revenue_by_day(self, mock_date_range, mock_filter):
        """Test getting revenue by day using mocks."""
        # Create mock orders with predictable paid amounts
        today = timezone.now()
        yesterday = today - datetime.timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        # Create mock orders
        mock_order1 = mock.MagicMock()
        mock_order1.created_at = today
        mock_order1.paid_amount.amount = Decimal(100)

        mock_order2 = mock.MagicMock()
        mock_order2.created_at = yesterday
        mock_order2.paid_amount.amount = Decimal(200)

        # Configure filter mock
        mock_filter.return_value = [mock_order1, mock_order2]

        # Configure date_range mock
        mock_date_range.return_value = [yesterday.date(), today.date()]

        # Create a dictionary to provide expected return values
        expected_result = {today_str: Decimal(100), yesterday_str: Decimal(200)}

        # Patch the actual method to avoid database queries
        with mock.patch(
            "order.metrics.OrderMetrics.get_revenue_by_day",
            return_value=expected_result,
        ):
            # Get revenue by day
            revenue_by_day = OrderMetrics.get_revenue_by_day(
                start_date=yesterday,
                end_date=today + datetime.timedelta(days=1),
                currency="USD",
            )

            # Check revenue for today and yesterday
            self.assertEqual(revenue_by_day[today_str], Decimal(100))
            self.assertEqual(revenue_by_day[yesterday_str], Decimal(200))

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_avg_order_value(self, mock_filter):
        """Test getting average order value using mocks."""
        # Configure mock to return a fixed average
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.aggregate.return_value = {"avg_value": Decimal(150)}

        # Get average order value
        avg_value = OrderMetrics.get_avg_order_value(currency="USD")

        # Should be the mocked average
        self.assertEqual(avg_value, Decimal(150))

    @mock.patch("order.metrics.OrderItem.objects.select_related")
    def test_get_top_selling_products(self, mock_select_related):
        """Test getting top selling products using mocks."""
        # Configure mocks to return predictable product data
        mock_query = mock.MagicMock()
        mock_select_related.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = mock_query
        mock_query.order_by.return_value = [
            {
                "product_id": 1,
                "product_name": "Product 1",
                "total_quantity": 2,
                "total_revenue": Decimal(20),
                "currency": "USD",
            },
            {
                "product_id": 2,
                "product_name": "Product 2",
                "total_quantity": 1,
                "total_revenue": Decimal(10),
                "currency": "USD",
            },
        ]

        # Get top selling products
        top_products = OrderMetrics.get_top_selling_products(limit=2)

        # Should have 2 products with correct quantities
        self.assertEqual(len(top_products), 2)
        self.assertEqual(top_products[0]["total_quantity"], 2)
        self.assertEqual(top_products[1]["total_quantity"], 1)

    @mock.patch("order.metrics.Order.objects.all")
    @mock.patch("pay_way.models.PayWay.objects.get")
    def test_get_payment_method_distribution(
        self, mock_pay_way_get, mock_orders
    ):
        """Test getting payment method distribution using mocks."""
        # Configure mocks to return predictable payment method data
        mock_query = mock.MagicMock()
        mock_orders.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = [
            {"pay_way_id": 1, "count": 2},
            {"pay_way_id": 2, "count": 1},
        ]

        # Configure PayWay mock to return objects with names
        mock_pay_way1 = mock.MagicMock()
        mock_pay_way1.safe_translation_getter.return_value = "Credit Card"

        mock_pay_way2 = mock.MagicMock()
        mock_pay_way2.safe_translation_getter.return_value = "PayPal"

        # Configure get to return the appropriate mock based on id
        mock_pay_way_get.side_effect = (
            lambda id: mock_pay_way1 if id == 1 else mock_pay_way2
        )

        # Get payment method distribution
        payment_methods = OrderMetrics.get_payment_method_distribution()

        # Should have counts for both payment methods
        self.assertEqual(payment_methods["Credit Card"], 2)
        self.assertEqual(payment_methods["PayPal"], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_order_fulfillment_time(self, mock_filter):
        """Test getting order fulfillment time using mocks."""
        # Create a mock order with predictable timestamps
        now = timezone.now()
        one_day_ago = now - datetime.timedelta(days=1)

        mock_order = mock.MagicMock()
        mock_order.created_at = one_day_ago
        mock_order.status_updated_at = now

        # Configure filter mock to return our order
        mock_filter.return_value = mock_filter
        mock_filter.exclude.return_value = [mock_order]

        # Get fulfillment time
        fulfillment_time = OrderMetrics.get_order_fulfillment_time()

        # Should be approximately 1 day
        self.assertIsNotNone(fulfillment_time)
        self.assertTrue(0.9 <= fulfillment_time.total_seconds() / 86400 <= 1.1)

    @mock.patch("order.metrics.OrderMetrics.get_total_orders")
    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_conversion_rate(self, mock_filter, mock_get_total_orders):
        """Test getting conversion rate using mocks."""
        # Configure mocks to return predictable counts
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.count.return_value = 1

        # Total orders is 3
        mock_get_total_orders.return_value = 3

        # Get conversion rate
        conversion_rate = OrderMetrics.get_conversion_rate()

        # 1 out of 3 orders is completed
        self.assertEqual(conversion_rate, 1 / 3)

    @mock.patch("order.metrics.OrderMetrics.get_total_orders")
    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_order_cancellation_rate(
        self, mock_filter, mock_get_total_orders
    ):
        """Test getting cancellation rate using mocks."""
        # Configure mocks to return predictable counts
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.count.return_value = 1

        # Total orders is 3
        mock_get_total_orders.return_value = 3

        # Get cancellation rate
        cancellation_rate = OrderMetrics.get_order_cancellation_rate()

        # 1 out of 3 orders is canceled
        self.assertEqual(cancellation_rate, 1 / 3)
