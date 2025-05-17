from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from django.core.exceptions import ValidationError
from djmoney.money import Money

from order.models.item import OrderItem, OrderItemManager, OrderItemQuerySet


class OrderItemModelTestCase(TestCase):
    def setUp(self):
        self.order_item = Mock(spec=OrderItem)
        self.order_item.id = 1
        self.order_item.uuid = "test-uuid-1234"
        self.order_item.price = Money("50.00", "USD")
        self.order_item.quantity = 2
        self.order_item.original_quantity = 2
        self.order_item.refunded_quantity = 0

        self.order_item.order = Mock()

        self.order_item.product = Mock()
        self.order_item.product.stock = 10
        self.order_item.product.name = "Test Product"

    def test_str_representation(self):
        product_name = "Test Product"
        quantity = 2

        expected_str = f"{product_name} (x{quantity})"

        self.assertEqual(expected_str, "Test Product (x2)")

    def test_get_ordering_queryset(self):
        mock_qs = Mock()

        self.order_item.order.items.all.return_value = mock_qs

        result = OrderItem.get_ordering_queryset(self.order_item)

        self.assertEqual(result, mock_qs)
        self.order_item.order.items.all.assert_called_once()

    def test_clean_valid_quantity(self):
        self.order_item.quantity = 5

        result = OrderItem.clean(self.order_item)

        self.assertIsNone(result)

    def test_clean_invalid_quantity(self):
        self.order_item.quantity = 0

        with self.assertRaises(ValidationError):
            OrderItem.clean(self.order_item)

    def test_clean_refunded_quantity_too_high(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 6

        with self.assertRaises(ValidationError):
            OrderItem.clean(self.order_item)

    def test_clean_insufficient_stock(self):
        self.order_item.quantity = 10
        self.order_item.product.stock = 5
        self.order_item.pk = None

        with self.assertRaises(ValidationError):
            OrderItem.clean(self.order_item)

    def test_clean_existing_item_stock_not_checked(self):
        self.order_item.quantity = 15
        self.order_item.product.stock = 10
        self.order_item.pk = 1

        result = OrderItem.clean(self.order_item)

        self.assertIsNone(result)

    def test_save_new_item(self):
        self.order_item.pk = None
        self.order_item.original_quantity = None
        original_quantity_value = self.order_item.quantity

        def mock_save_implementation(instance, *args, **kwargs):
            if not instance.pk and instance.original_quantity is None:
                instance.original_quantity = instance.quantity

        with patch.object(OrderItem, "save"):
            mock_save_implementation(self.order_item)

        self.assertEqual(
            self.order_item.original_quantity, original_quantity_value
        )

    def test_save_existing_item(self):
        self.order_item.pk = 1
        self.order_item.original_quantity = 2
        original_value = self.order_item.original_quantity

        with patch.object(
            OrderItem, "save", lambda self, *args, **kwargs: None
        ):
            OrderItem.save(self.order_item)

        self.assertEqual(self.order_item.original_quantity, original_value)

    def test_total_price_property(self):
        self.order_item.price = Money("50.00", "USD")
        self.order_item.quantity = 3
        expected_total = Money("150.00", "USD")

        result = OrderItem.total_price.__get__(self.order_item)

        self.assertEqual(result, expected_total)

    def test_net_quantity_property(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 2
        expected_net = 3

        result = OrderItem.net_quantity.__get__(self.order_item)

        self.assertEqual(result, expected_net)

    def test_net_price_property(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 2

        def mock_net_price(item):
            net_quantity = item.quantity - item.refunded_quantity
            return Money(
                amount=Decimal("50.00") * Decimal(net_quantity), currency="USD"
            )

        expected_net = mock_net_price(self.order_item)

        with patch.object(
            OrderItem, "net_price", mock_net_price(self.order_item)
        ):
            self.assertEqual(expected_net, Money("150.00", "USD"))

    def test_refunded_amount_property_no_refund(self):
        self.order_item.refunded_quantity = 0
        expected_refund = Money("0.00", "USD")

        result = OrderItem.refunded_amount.__get__(self.order_item)

        self.assertEqual(result, expected_refund)

    def test_refunded_amount_property_partial_refund(self):
        self.order_item.refunded_quantity = 2
        self.order_item.quantity = 5
        self.order_item.price = Money("50.00", "USD")
        expected_refund = Money("100.00", "USD")

        result = OrderItem.refunded_amount.__get__(self.order_item)

        self.assertEqual(result, expected_refund)

    def test_refund_invalid_quantity(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        with self.assertRaises(ValidationError):
            OrderItem.refund(self.order_item, -1)

        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_refund_too_much(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        with self.assertRaises(ValidationError):
            OrderItem.refund(self.order_item, 6)

        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_refund_partial(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        result = OrderItem.refund(self.order_item, 2)

        self.assertTrue(result)
        self.assertEqual(self.order_item.refunded_quantity, 2)

    def test_refund_full(self):
        self.order_item.quantity = 5
        self.order_item.refunded_quantity = 0

        result = OrderItem.refund(self.order_item, 5)

        self.assertTrue(result)
        self.assertEqual(self.order_item.refunded_quantity, 5)


class OrderItemQuerySetTestCase(TestCase):
    def setUp(self):
        self.queryset = Mock(spec=OrderItemQuerySet)

        self.queryset.filter.return_value = self.queryset

        self.queryset.annotate.return_value = self.queryset

        self.settings_patch = patch("django.conf.settings")
        self.mock_settings = self.settings_patch.start()
        self.mock_settings.DEFAULT_CURRENCY = "USD"

    def tearDown(self):
        self.settings_patch.stop()

    def test_for_order(self):
        order_id = 1

        result = OrderItemQuerySet.for_order(self.queryset, order_id)

        self.queryset.filter.assert_called_once_with(order_id=order_id)
        self.assertEqual(result, self.queryset)

    def test_for_product(self):
        product_id = 1

        result = OrderItemQuerySet.for_product(self.queryset, product_id)

        self.queryset.filter.assert_called_once_with(product_id=product_id)
        self.assertEqual(result, self.queryset)

    def test_with_product_data(self):
        prefetch_result = Mock()
        select_result = Mock()
        select_result.prefetch_related.return_value = prefetch_result
        self.queryset.select_related.return_value = select_result

        result = OrderItemQuerySet.with_product_data(self.queryset)

        self.queryset.select_related.assert_called_once_with("product")
        select_result.prefetch_related.assert_called_once()
        self.assertEqual(result, prefetch_result)

    def test_annotate_total_price(self):
        with (
            patch("django.db.models.F"),
            patch("django.db.models.ExpressionWrapper"),
        ):
            result = OrderItemQuerySet.annotate_total_price(self.queryset)

        self.queryset.annotate.assert_called_once()
        self.assertEqual(result, self.queryset)

    def test_sum_quantities(self):
        with patch("django.db.models.Sum"):
            self.queryset.aggregate.return_value = {"total_quantity": 10}

            result = OrderItemQuerySet.sum_quantities(self.queryset)

        self.assertEqual(result, 10)

    def test_sum_quantities_no_results(self):
        with patch("django.db.models.Sum"):
            self.queryset.aggregate.return_value = {"total_quantity": None}

            result = OrderItemQuerySet.sum_quantities(self.queryset)

        self.assertEqual(result, 0)

    def test_total_items_cost(self):
        self.queryset.annotate_total_price.return_value = self.queryset

        self.queryset.aggregate.return_value = {"total": Decimal("100.00")}

        mock_item = Mock()
        mock_price = Mock()
        type(mock_price).currency = "USD"
        type(mock_item).price = PropertyMock(return_value=mock_price)
        self.queryset.first.return_value = mock_item

        result = OrderItemQuerySet.total_items_cost(self.queryset)

        expected_money = Money("100.00", "USD")
        self.assertEqual(result, expected_money)

        self.queryset.annotate_total_price.assert_called_once()
        self.queryset.aggregate.assert_called_once()

    def test_total_items_cost_no_items(self):
        self.queryset.annotate_total_price.return_value = self.queryset

        self.queryset.aggregate.return_value = {"total": None}

        mock_item = Mock()
        mock_price = Mock()
        type(mock_price).currency = "USD"
        type(mock_item).price = PropertyMock(return_value=mock_price)
        self.queryset.first.return_value = mock_item

        result = OrderItemQuerySet.total_items_cost(self.queryset)

        expected_money = Money("0", "USD")
        self.assertEqual(result, expected_money)

        self.queryset.annotate_total_price.assert_called_once()
        self.queryset.aggregate.assert_called_once()


class OrderItemManagerTestCase(TestCase):
    def setUp(self):
        self.manager = Mock(spec=OrderItemManager)

        self.queryset = Mock(spec=OrderItemQuerySet)
        self.manager.get_queryset.return_value = self.queryset

        self.queryset.for_order.return_value = "for_order_result"
        self.queryset.for_product.return_value = "for_product_result"
        self.queryset.with_product_data.return_value = (
            "with_product_data_result"
        )
        self.queryset.sum_quantities.return_value = 5
        self.queryset.total_items_cost.return_value = Money("100.00", "USD")

    def test_for_order(self):
        order_id = 1

        result = OrderItemManager.for_order(self.manager, order_id)

        self.manager.get_queryset.assert_called_once()
        self.queryset.for_order.assert_called_once_with(order_id)
        self.assertEqual(result, "for_order_result")

    def test_for_product(self):
        product_id = 1

        result = OrderItemManager.for_product(self.manager, product_id)

        self.manager.get_queryset.assert_called_once()
        self.queryset.for_product.assert_called_once_with(product_id)
        self.assertEqual(result, "for_product_result")

    def test_with_product_data(self):
        result = OrderItemManager.with_product_data(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.with_product_data.assert_called_once()
        self.assertEqual(result, "with_product_data_result")

    def test_sum_quantities(self):
        result = OrderItemManager.sum_quantities(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.sum_quantities.assert_called_once()
        self.assertEqual(result, 5)

    def test_total_items_cost(self):
        result = OrderItemManager.total_items_cost(self.manager)

        self.manager.get_queryset.assert_called_once()
        self.queryset.total_items_cost.assert_called_once()
        self.assertEqual(result, Money("100.00", "USD"))
