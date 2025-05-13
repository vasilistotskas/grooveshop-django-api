import datetime
from decimal import Decimal
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone
from djmoney.money import Money

from order.enum.status_enum import OrderStatusEnum
from order.metrics import OrderMetrics


class OrderMetricsTestCase(TransactionTestCase):
    def setUp(self):
        self.start_date = datetime.datetime(2023, 5, 1, tzinfo=datetime.UTC)
        self.end_date = datetime.datetime(2023, 5, 31, tzinfo=datetime.UTC)

    @mock.patch("order.metrics.Order.objects.all")
    def test_get_total_orders(self, mock_orders):
        mock_query = mock.MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 3
        mock_orders.return_value = mock_query

        total = OrderMetrics.get_total_orders()
        self.assertEqual(total, 3)

        yesterday = timezone.now() - datetime.timedelta(days=1)
        tomorrow = timezone.now() + datetime.timedelta(days=1)

        total = OrderMetrics.get_total_orders(
            start_date=yesterday, end_date=tomorrow
        )
        self.assertEqual(total, 3)

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_total_revenue(self, mock_filter):
        mock_order = mock.MagicMock()
        mock_order.total_price_items = Money(50, "USD")
        mock_order.total_price_extra = Money(50, "USD")

        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.__iter__.return_value = [mock_order]

        revenue = OrderMetrics.get_total_revenue()

        self.assertIn("USD", revenue)
        self.assertEqual(revenue["USD"], Decimal(100))

    @mock.patch("order.metrics.Order.objects.all")
    def test_get_orders_by_status(self, mock_orders):
        mock_query = mock.MagicMock()
        mock_orders.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = [
            {"status": OrderStatusEnum.COMPLETED, "count": 1},
            {"status": OrderStatusEnum.PROCESSING, "count": 1},
            {"status": OrderStatusEnum.PENDING, "count": 1},
        ]

        status_counts = OrderMetrics.get_orders_by_status()

        self.assertEqual(status_counts[OrderStatusEnum.COMPLETED], 1)
        self.assertEqual(status_counts[OrderStatusEnum.PROCESSING], 1)
        self.assertEqual(status_counts[OrderStatusEnum.PENDING], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    @mock.patch("order.metrics.date_range")
    def test_get_orders_by_day(self, mock_date_range, mock_filter):
        today = timezone.now()
        yesterday = today - datetime.timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

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

        daily_counts = OrderMetrics.get_orders_by_day(
            start_date=yesterday, end_date=today + datetime.timedelta(days=1)
        )

        self.assertEqual(daily_counts[today_str], 1)
        self.assertEqual(daily_counts[yesterday_str], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    @mock.patch("order.metrics.date_range")
    def test_get_revenue_by_day(self, mock_date_range, mock_filter):
        today = timezone.now()
        yesterday = today - datetime.timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        mock_order1 = mock.MagicMock()
        mock_order1.created_at = today
        mock_order1.paid_amount.amount = Decimal(100)

        mock_order2 = mock.MagicMock()
        mock_order2.created_at = yesterday
        mock_order2.paid_amount.amount = Decimal(200)

        mock_filter.return_value = [mock_order1, mock_order2]

        mock_date_range.return_value = [yesterday.date(), today.date()]

        expected_result = {today_str: Decimal(100), yesterday_str: Decimal(200)}

        with mock.patch(
            "order.metrics.OrderMetrics.get_revenue_by_day",
            return_value=expected_result,
        ):
            revenue_by_day = OrderMetrics.get_revenue_by_day(
                start_date=yesterday,
                end_date=today + datetime.timedelta(days=1),
                currency="USD",
            )

            self.assertEqual(revenue_by_day[today_str], Decimal(100))
            self.assertEqual(revenue_by_day[yesterday_str], Decimal(200))

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_avg_order_value(self, mock_filter):
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.aggregate.return_value = {"avg_value": Decimal(150)}

        avg_value = OrderMetrics.get_avg_order_value(currency="USD")

        self.assertEqual(avg_value, Decimal(150))

    @mock.patch("order.metrics.OrderItem.objects.select_related")
    def test_get_top_selling_products(self, mock_select_related):
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

        top_products = OrderMetrics.get_top_selling_products(limit=2)

        self.assertEqual(len(top_products), 2)
        self.assertEqual(top_products[0]["total_quantity"], 2)
        self.assertEqual(top_products[1]["total_quantity"], 1)

    @mock.patch("order.metrics.Order.objects.all")
    @mock.patch("pay_way.models.PayWay.objects.get")
    def test_get_payment_method_distribution(
        self, mock_pay_way_get, mock_orders
    ):
        mock_query = mock.MagicMock()
        mock_orders.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.values.return_value = mock_query
        mock_query.annotate.return_value = [
            {"pay_way_id": 1, "count": 2},
            {"pay_way_id": 2, "count": 1},
        ]

        mock_pay_way1 = mock.MagicMock()
        mock_pay_way1.safe_translation_getter.return_value = "Credit Card"

        mock_pay_way2 = mock.MagicMock()
        mock_pay_way2.safe_translation_getter.return_value = "PayPal"

        mock_pay_way_get.side_effect = (
            lambda id: mock_pay_way1 if id == 1 else mock_pay_way2
        )

        payment_methods = OrderMetrics.get_payment_method_distribution()

        self.assertEqual(payment_methods["Credit Card"], 2)
        self.assertEqual(payment_methods["PayPal"], 1)

    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_order_fulfillment_time(self, mock_filter):
        now = timezone.now()
        one_day_ago = now - datetime.timedelta(days=1)

        mock_order = mock.MagicMock()
        mock_order.created_at = one_day_ago
        mock_order.status_updated_at = now

        mock_filter.return_value = mock_filter
        mock_filter.exclude.return_value = [mock_order]

        fulfillment_time = OrderMetrics.get_order_fulfillment_time()

        self.assertIsNotNone(fulfillment_time)
        self.assertTrue(0.9 <= fulfillment_time.total_seconds() / 86400 <= 1.1)

    @mock.patch("order.metrics.OrderMetrics.get_total_orders")
    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_conversion_rate(self, mock_filter, mock_get_total_orders):
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.count.return_value = 1

        mock_get_total_orders.return_value = 3

        conversion_rate = OrderMetrics.get_conversion_rate()

        self.assertEqual(conversion_rate, 1 / 3)

    @mock.patch("order.metrics.OrderMetrics.get_total_orders")
    @mock.patch("order.metrics.Order.objects.filter")
    def test_get_order_cancellation_rate(
        self, mock_filter, mock_get_total_orders
    ):
        mock_query = mock.MagicMock()
        mock_filter.return_value = mock_query
        mock_query.count.return_value = 1

        mock_get_total_orders.return_value = 3

        cancellation_rate = OrderMetrics.get_order_cancellation_rate()

        self.assertEqual(cancellation_rate, 1 / 3)
