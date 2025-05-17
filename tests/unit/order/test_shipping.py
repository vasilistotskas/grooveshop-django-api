from decimal import Decimal
from unittest import TestCase, mock

from djmoney.money import Money

from order.shipping import (
    FedExCarrier,
    ShippingMethodType,
    ShippingOption,
    ShippingService,
    UPSCarrier,
    get_shipping_carrier,
)


class ShippingMethodTypeTestCase(TestCase):
    def test_shipping_method_type_enum(self):
        self.assertEqual(ShippingMethodType.STANDARD.value, "STANDARD")
        self.assertEqual(ShippingMethodType.EXPRESS.value, "EXPRESS")
        self.assertEqual(ShippingMethodType.NEXT_DAY.value, "NEXT_DAY")
        self.assertEqual(ShippingMethodType.ECONOMY.value, "ECONOMY")
        self.assertEqual(
            ShippingMethodType.INTERNATIONAL.value, "INTERNATIONAL"
        )
        self.assertEqual(ShippingMethodType.PICKUP.value, "PICKUP")


class ShippingOptionTestCase(TestCase):
    def test_shipping_option_creation(self):
        option = ShippingOption(
            id="test_id",
            name="Test Option",
            method_type=ShippingMethodType.STANDARD,
            price=Money(amount=Decimal("10.00"), currency="USD"),
            estimated_delivery_min=3,
            estimated_delivery_max=5,
            carrier="Test Carrier",
            carrier_service_code="TEST_CODE",
            description="Test description",
        )

        self.assertEqual(option.id, "test_id")
        self.assertEqual(option.name, "Test Option")
        self.assertEqual(option.method_type, ShippingMethodType.STANDARD)
        self.assertEqual(option.price.amount, Decimal("10.00"))
        self.assertEqual(str(option.price.currency), "USD")
        self.assertEqual(option.estimated_delivery_min, 3)
        self.assertEqual(option.estimated_delivery_max, 5)
        self.assertEqual(option.carrier, "Test Carrier")
        self.assertEqual(option.carrier_service_code, "TEST_CODE")
        self.assertEqual(option.description, "Test description")

    def test_estimated_delivery_date(self):
        option = ShippingOption(
            id="test_id",
            name="Test Option",
            method_type=ShippingMethodType.STANDARD,
            price=Money(amount=Decimal("10.00"), currency="USD"),
            estimated_delivery_min=3,
            estimated_delivery_max=5,
            carrier="Test Carrier",
            carrier_service_code="TEST_CODE",
        )

        min_date = option.estimated_delivery_date_min
        max_date = option.estimated_delivery_date_max

        self.assertEqual((max_date - min_date).days, 2)


@mock.patch("order.shipping.settings")
class FedExCarrierTestCase(TestCase):
    def setUp(self):
        self.from_country = mock.MagicMock()
        self.from_country.alpha_2 = "US"
        self.to_country = mock.MagicMock()
        self.to_country.alpha_2 = "US"
        self.to_region = mock.MagicMock()

        self.weight = Decimal("2.5")
        self.dimensions = {
            "length": Decimal("10"),
            "width": Decimal("5"),
            "height": Decimal("3"),
        }

    def test_init(self, mock_settings):
        mock_settings.FEDEX_API_KEY = "test_api_key"
        mock_settings.FEDEX_ACCOUNT_NUMBER = "test_account_number"

        carrier = FedExCarrier()

        self.assertEqual(carrier.api_key, "test_api_key")
        self.assertEqual(carrier.account_number, "test_account_number")

    @mock.patch("order.shipping.logger")
    def test_get_shipping_options_domestic(self, mock_logger, mock_settings):
        carrier = FedExCarrier()

        options = carrier.get_shipping_options(
            weight=self.weight,
            dimensions=self.dimensions,
            from_country=self.from_country,
            to_country=self.to_country,
            to_region=self.to_region,
            to_postal_code="12345",
        )

        self.assertEqual(len(options), 3)
        self.assertEqual(options[0].carrier, "FedEx")
        self.assertEqual(options[0].method_type, ShippingMethodType.STANDARD)
        self.assertEqual(options[1].method_type, ShippingMethodType.EXPRESS)
        self.assertEqual(options[2].method_type, ShippingMethodType.NEXT_DAY)

        mock_logger.info.assert_called_once()

    @mock.patch("order.shipping.logger")
    def test_get_shipping_options_international(
        self, mock_logger, mock_settings
    ):
        carrier = FedExCarrier()
        international_country = mock.MagicMock()
        international_country.alpha_2 = "GB"

        options = carrier.get_shipping_options(
            weight=self.weight,
            dimensions=self.dimensions,
            from_country=self.from_country,
            to_country=international_country,
            to_region=self.to_region,
            to_postal_code="SW1A 1AA",
        )

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].carrier, "FedEx")
        self.assertEqual(
            options[0].method_type, ShippingMethodType.INTERNATIONAL
        )

        mock_logger.info.assert_called_once()

    @mock.patch("order.shipping.FedExCarrier.create_shipment")
    @mock.patch("order.shipping.logger")
    def test_create_shipment(
        self, mock_logger, mock_create_shipment, mock_settings
    ):
        mock_create_shipment.return_value = (
            True,
            {
                "tracking_number": "FX123456789",
                "carrier": "FedEx",
                "shipping_method": "GROUND",
            },
        )

        carrier = FedExCarrier()
        order_id = "test_order_id"
        shipping_option_id = "fedex_ground"
        address_data = {
            "name": "John Doe",
            "address1": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "postal_code": "12345",
            "country": "US",
        }

        success, shipment_data = carrier.create_shipment(
            order_id=order_id,
            shipping_option_id=shipping_option_id,
            address_data=address_data,
        )

        self.assertTrue(success)
        self.assertEqual(shipment_data["tracking_number"][:2], "FX")
        self.assertEqual(shipment_data["carrier"], "FedEx")
        self.assertEqual(shipment_data["shipping_method"], "GROUND")

        mock_create_shipment.assert_called_once()

    @mock.patch("order.shipping.logger")
    def test_get_tracking_info(self, mock_logger, mock_settings):
        carrier = FedExCarrier()
        tracking_number = "FEDEX123456789"

        tracking_info = carrier.get_tracking_info(tracking_number)

        self.assertEqual(tracking_info["carrier"], "FedEx")
        self.assertEqual(tracking_info["tracking_number"], tracking_number)
        self.assertIn("status", tracking_info)
        self.assertIn("estimated_delivery", tracking_info)

        mock_logger.info.assert_called_once()


class ShippingServiceTestCase(TestCase):
    def setUp(self):
        self.from_country = mock.MagicMock()
        self.from_country.alpha_2 = "US"
        self.to_country = mock.MagicMock()
        self.to_country.alpha_2 = "US"
        self.to_region = mock.MagicMock()

        self.weight = Decimal("2.5")
        self.dimensions = {
            "length": Decimal("10"),
            "width": Decimal("5"),
            "height": Decimal("3"),
        }

    @mock.patch("order.shipping.get_shipping_carrier")
    def test_get_available_shipping_options(self, mock_get_carrier):
        fedex_carrier = mock.MagicMock()
        fedex_options = [
            ShippingOption(
                id="fedex_ground",
                name="FedEx Ground",
                method_type=ShippingMethodType.STANDARD,
                price=Money(amount="12.99", currency="USD"),
                estimated_delivery_min=3,
                estimated_delivery_max=5,
                carrier="FedEx",
                carrier_service_code="GROUND",
            )
        ]
        fedex_carrier.get_shipping_options.return_value = fedex_options

        ups_carrier = mock.MagicMock()
        ups_options = [
            ShippingOption(
                id="ups_ground",
                name="UPS Ground",
                method_type=ShippingMethodType.STANDARD,
                price=Money(amount="11.99", currency="USD"),
                estimated_delivery_min=2,
                estimated_delivery_max=4,
                carrier="UPS",
                carrier_service_code="GROUND",
            )
        ]
        ups_carrier.get_shipping_options.return_value = ups_options

        mock_get_carrier.side_effect = (
            lambda carrier: fedex_carrier if carrier == "fedex" else ups_carrier
        )

        options = ShippingService.get_available_shipping_options(
            order_weight=self.weight,
            order_dimensions=self.dimensions,
            from_country=self.from_country,
            to_country=self.to_country,
            to_region=self.to_region,
            to_postal_code="12345",
        )

        self.assertEqual(len(options), 2)

        self.assertEqual(options[0].name, "UPS Ground")
        self.assertEqual(options[1].name, "FedEx Ground")

    @mock.patch("order.shipping.get_shipping_carrier")
    def test_create_shipment(self, mock_get_carrier):
        carrier = mock.MagicMock()
        carrier.create_shipment.return_value = (
            True,
            {"tracking_number": "TEST123"},
        )
        mock_get_carrier.return_value = carrier

        success, shipment_data = ShippingService.create_shipment(
            order_id="test_order_id",
            carrier="fedex",
            shipping_option_id="fedex_ground",
            address_data={},
        )

        self.assertTrue(success)
        self.assertEqual(shipment_data["tracking_number"], "TEST123")
        carrier.create_shipment.assert_called_once()

    @mock.patch("order.shipping.get_shipping_carrier")
    def test_get_tracking_info(self, mock_get_carrier):
        carrier = mock.MagicMock()
        carrier.get_tracking_info.return_value = {
            "tracking_number": "TEST123",
            "status": "In Transit",
        }
        mock_get_carrier.return_value = carrier

        tracking_info = ShippingService.get_tracking_info(
            tracking_number="TEST123", carrier="fedex"
        )

        self.assertEqual(tracking_info["tracking_number"], "TEST123")
        self.assertEqual(tracking_info["status"], "In Transit")
        carrier.get_tracking_info.assert_called_once()


class GetShippingCarrierTestCase(TestCase):
    def test_get_valid_carrier(self):
        carrier = get_shipping_carrier("fedex")
        self.assertIsInstance(carrier, FedExCarrier)

        carrier = get_shipping_carrier("ups")
        self.assertIsInstance(carrier, UPSCarrier)

    def test_get_invalid_carrier(self):
        with self.assertRaises(ValueError):
            get_shipping_carrier("invalid_carrier")
