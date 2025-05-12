import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from djmoney.money import Money

from order.metrics import OrderMetrics


class OrderMetricsTestCase(TestCase):
    """Test case for the OrderMetrics class."""

    def setUp(self):
        """Set up test data."""
        self.start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
        self.end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

    def test_get_total_orders(self):
        """Test getting total order count."""
        # Make the mock decorator pass through the original function
        # Mock Order.objects.all().count() directly to return 1
        with patch("order.models.order.Order.objects.all") as mock_all:
            mock_query = MagicMock()
            mock_all.return_value = mock_query

            # Configure the count method to return 1 regardless of filter parameters
            mock_query.filter.return_value = mock_query
            mock_query.count.return_value = 1

            # Call with date range covering all orders - use timezone-aware dates
            start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
            end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

            total = OrderMetrics.get_total_orders(
                start_date=start_date, end_date=end_date
            )

            # Just verify we get the expected count
            self.assertEqual(total, 1)

    @patch("order.metrics.Order.objects.filter")
    def test_get_total_revenue(self, mock_filter):
        """Test getting total revenue."""

        # Set up a mock query that returns our completed order with USD currency
        mock_order = MagicMock()
        mock_order.total_price_items = Money("50.00", "USD")
        mock_order.total_price_extra = Money("50.00", "USD")

        # Configure the mock to return our test order
        mock_filter.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.__iter__.return_value = [mock_order]

        # Call with date range covering all orders
        start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

        revenue = OrderMetrics.get_total_revenue(
            start_date=start_date, end_date=end_date
        )

        # Should have one currency with revenue
        self.assertEqual(len(revenue), 1)
        self.assertIn("USD", revenue)
        # Our mock returns $50 + $50 = $100
        self.assertEqual(revenue["USD"], Decimal("100.00"))

    def test_get_orders_by_status(self):
        """Test getting orders grouped by status."""

        # Mock the implementation to return fixed status counts
        with patch("order.models.order.Order.objects.all") as mock_all:
            # Set up the mock query chain
            mock_query = MagicMock()
            mock_all.return_value = mock_query

            # Configure filter and values methods
            mock_query.filter.return_value = mock_query
            mock_query.values.return_value = mock_query

            # Set up the annotate to return our test data
            mock_query.annotate.return_value = [
                {"status": "COMPLETED", "count": 1},
                {"status": "PROCESSING", "count": 1},
                {"status": "CANCELED", "count": 1},
            ]

            # Call with date range
            start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
            end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

            status_counts = OrderMetrics.get_orders_by_status(
                start_date=start_date, end_date=end_date
            )

            # Verify the counts
            self.assertEqual(status_counts["COMPLETED"], 1)
            self.assertEqual(status_counts["PROCESSING"], 1)
            self.assertEqual(status_counts["CANCELED"], 1)

    def test_get_orders_by_day(self):
        """Test getting orders grouped by day."""
        # Mock the implementation to return fixed daily counts
        with patch("order.metrics.Order.objects.filter") as mock_filter:
            # Set up the mock query chain
            mock_query = MagicMock()
            mock_filter.return_value = mock_query

            # Configure the chain of method calls
            mock_query.filter.return_value = mock_query
            mock_query.annotate.return_value = mock_query
            mock_query.values.return_value = mock_query

            # Set up the order_by to return our test data
            mock_query.order_by.return_value = [
                {
                    "day": datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC),
                    "count": 1,
                },
                {
                    "day": datetime.datetime(2023, 5, 15, tzinfo=datetime.UTC),
                    "count": 1,
                },
                {
                    "day": datetime.datetime(2023, 5, 30, tzinfo=datetime.UTC),
                    "count": 1,
                },
            ]

            # Mock date_range to return a fixed list of dates
            with patch("order.metrics.date_range") as mock_date_range:
                mock_date_range.return_value = [
                    datetime.date(2023, 5, 1),
                    datetime.date(2023, 5, 15),
                    datetime.date(2023, 5, 30),
                ]

                # Call with date range
                start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
                end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

                daily_counts = OrderMetrics.get_orders_by_day(
                    start_date=start_date, end_date=end_date
                )

                # Only check the days with orders
                self.assertEqual(daily_counts["2023-05-01"], 1)
                self.assertEqual(daily_counts["2023-05-15"], 1)
                self.assertEqual(daily_counts["2023-05-30"], 1)

    @patch("order.metrics.OrderItem.objects")
    def test_get_top_selling_products(self, mock_orderitem_objects):
        mock_query = MagicMock()
        mock_orderitem_objects.select_related.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = mock_query
        mock_query.order_by.return_value = [
            {
                "product_id": 1,
                "product_name": "Test Product",
                "total_quantity": 2,
                "total_revenue": 100,
                "currency": "USD",
            }
        ]
        start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)
        products = OrderMetrics.get_top_selling_products(
            start_date=start_date, end_date=end_date
        )
        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product["product_id"], 1)
        self.assertEqual(product["total_quantity"], 2)

    @patch("order.metrics.Order.objects")
    def test_get_conversion_rate(self, mock_order_objects):
        """Test getting conversion rate."""

        # Configure mocks
        mock_query = MagicMock()
        mock_order_objects.filter.return_value = mock_query
        mock_query.count.return_value = 1

        # Mock get_total_orders to return a fixed value
        with patch.object(OrderMetrics, "get_total_orders", return_value=3):
            start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
            end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

            # Call the method
            conversion_rate = OrderMetrics.get_conversion_rate(
                start_date=start_date, end_date=end_date
            )

            # Verify the result
            self.assertAlmostEqual(conversion_rate, 1 / 3, places=2)

    @patch("order.metrics.Order.objects")
    def test_get_order_cancellation_rate(self, mock_order_objects):
        """Test getting cancellation rate."""

        # Configure mocks
        mock_query = MagicMock()
        mock_order_objects.filter.return_value = mock_query
        mock_query.count.return_value = 1

        # Mock get_total_orders to return a fixed value
        with patch.object(OrderMetrics, "get_total_orders", return_value=3):
            start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
            end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

            # Call the method
            cancellation_rate = OrderMetrics.get_order_cancellation_rate(
                start_date=start_date, end_date=end_date
            )

            # Verify the result
            self.assertAlmostEqual(cancellation_rate, 1 / 3, places=2)
