"""Unit tests for sample order data generator."""

import pytest
from decimal import Decimal
from core.email.sample_data import SampleOrderDataGenerator
from order.enum.status import OrderStatus


class TestSampleOrderDataGenerator:
    """Test suite for SampleOrderDataGenerator."""

    @pytest.fixture
    def generator(self):
        """Create a generator instance for testing."""
        return SampleOrderDataGenerator()

    def test_generator_initialization(self, generator):
        """Test that generator initializes correctly."""
        assert generator is not None
        assert hasattr(generator, "GREEK_FIRST_NAMES")
        assert hasattr(generator, "SAMPLE_PRODUCTS")

    def test_generate_order_returns_dict(self, generator):
        """Test that generate_order returns a dictionary."""
        order_data = generator.generate_order()
        assert isinstance(order_data, dict)

    def test_generated_order_has_required_fields(self, generator):
        """Test that generated order has all required fields."""
        data = generator.generate_order()

        # Check top-level structure
        assert "order" in data
        assert "items" in data

        order_data = data["order"]

        # Check required fields
        required_fields = [
            "id",
            "status",
            "first_name",
            "last_name",
            "email",
            "phone",
            "street",
            "city",
            "zipcode",
            "country",
            "paid_amount",
            "created_at",
        ]

        for field in required_fields:
            assert field in order_data, f"Missing required field: {field}"

    def test_generated_order_id_is_positive(self, generator):
        """Test that generated order ID is a positive integer."""
        data = generator.generate_order()
        order_data = data["order"]
        assert isinstance(order_data["id"], int)
        assert order_data["id"] > 0

    def test_generated_order_status_is_valid(self, generator):
        """Test that generated order status is valid."""
        data = generator.generate_order()
        order_data = data["order"]
        assert order_data["status"] in [s.value for s in OrderStatus]

    def test_generated_email_is_valid(self, generator):
        """Test that generated email has valid format."""
        data = generator.generate_order()
        order_data = data["order"]
        email = order_data["email"]
        assert isinstance(email, str)
        assert "@" in email
        assert "." in email

    def test_generated_phone_is_string(self, generator):
        """Test that generated phone is a string."""
        data = generator.generate_order()
        order_data = data["order"]
        assert isinstance(order_data["phone"], str)

    def test_generated_paid_amount_is_decimal(self, generator):
        """Test that generated paid amount is a string (formatted)."""
        data = generator.generate_order()
        order_data = data["order"]
        # paid_amount is formatted as string like "€123.45"
        assert isinstance(order_data["paid_amount"], str)
        assert "€" in order_data["paid_amount"]

    def test_generated_items_is_list(self, generator):
        """Test that generated items is a list."""
        data = generator.generate_order()
        assert isinstance(data["items"], list)
        assert len(data["items"]) > 0

    def test_generated_items_have_required_fields(self, generator):
        """Test that generated items have required fields."""
        data = generator.generate_order()

        required_item_fields = ["product", "quantity", "price", "total_price"]

        for item in data["items"]:
            for field in required_item_fields:
                assert field in item, f"Missing required item field: {field}"
            # Check product has name
            assert "name" in item["product"]

    def test_item_quantities_are_positive(self, generator):
        """Test that item quantities are positive integers."""
        data = generator.generate_order()
        for item in data["items"]:
            assert isinstance(item["quantity"], int)
            assert item["quantity"] > 0

    def test_item_prices_are_decimal(self, generator):
        """Test that item prices are Decimal values."""
        data = generator.generate_order()
        for item in data["items"]:
            assert isinstance(item["price"], Decimal)
            assert item["price"] > 0

    def test_item_total_price_calculation(self, generator):
        """Test that item total price is calculated correctly."""
        data = generator.generate_order()
        for item in data["items"]:
            expected_total = item["price"] * item["quantity"]
            assert item["total_price"] == expected_total

    def test_order_total_matches_items_sum(self, generator):
        """Test that order total matches sum of item totals (with shipping)."""
        data = generator.generate_order()
        items_total = sum(item["total_price"] for item in data["items"])

        # Extract numeric value from formatted string "€123.45"
        paid_amount_str = (
            data["order"]["paid_amount"].replace("€", "").replace(",", "")
        )
        paid_amount = Decimal(paid_amount_str)

        # Total should be items + shipping (€5 if < €50, else €0)
        shipping = (
            Decimal("5.00")
            if items_total < Decimal("50.00")
            else Decimal("0.00")
        )
        expected_total = items_total + shipping

        # Allow for small rounding differences
        assert abs(paid_amount - expected_total) < Decimal("0.01")

    def test_generate_multiple_orders_are_different(self, generator):
        """Test that generating multiple orders produces different data."""
        data1 = generator.generate_order()
        data2 = generator.generate_order()

        order1 = data1["order"]
        order2 = data2["order"]

        # IDs should be different
        assert order1["id"] != order2["id"]
        # At least some fields should be different
        assert (
            order1["first_name"] != order2["first_name"]
            or order1["last_name"] != order2["last_name"]
            or order1["email"] != order2["email"]
        )

    def test_generated_address_fields_are_strings(self, generator):
        """Test that address fields are strings."""
        data = generator.generate_order()
        order_data = data["order"]
        assert isinstance(order_data["street"], str)
        assert isinstance(order_data["city"], str)
        assert isinstance(order_data["zipcode"], str)
        assert isinstance(order_data["country"], str)

    def test_generated_names_are_strings(self, generator):
        """Test that name fields are strings."""
        data = generator.generate_order()
        order_data = data["order"]
        assert isinstance(order_data["first_name"], str)
        assert isinstance(order_data["last_name"], str)
        assert len(order_data["first_name"]) > 0
        assert len(order_data["last_name"]) > 0
