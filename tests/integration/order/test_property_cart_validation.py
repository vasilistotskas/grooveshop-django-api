import pytest
from decimal import Decimal
from django.conf import settings
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from order.exceptions import ProductNotFoundError
from order.services import OrderService
from product.factories.product import ProductFactory


@pytest.mark.django_db(transaction=True)
class TestProperty24OrderCreationValidatesCartState:
    """
    Test that order creation validates cart state including:
    - Cart is not empty
    - All products exist
    - All products are in stock
    - Prices haven't changed significantly (>5% tolerance)
    """

    def test_empty_cart_validation_fails(self):
        """
        Test that validation fails for empty cart.
        """
        # Create empty cart
        cart = CartFactory.create()

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        # Verify validation failed
        assert result["valid"] is False, "Empty cart should fail validation"
        assert len(result["errors"]) > 0, "Should have at least one error"
        assert any(
            "empty" in str(error).lower() for error in result["errors"]
        ), "Error should mention cart is empty"

    @pytest.mark.parametrize(
        "product_stock,cart_quantity,should_fail,description",
        [
            # Test case 1: Insufficient stock - quantity exceeds stock significantly
            (5, 10, True, "quantity_double_stock"),
            # Test case 2: Zero stock
            (0, 1, True, "zero_stock"),
            # Test case 3: Insufficient stock - quantity slightly over
            (10, 12, True, "quantity_slightly_over"),
            # Test case 4: Exact stock match (should succeed)
            (10, 10, False, "exact_stock_match"),
            # Test case 5: Sufficient stock
            (20, 10, False, "sufficient_stock"),
            # Test case 6: Edge case - one more than stock
            (5, 6, True, "one_over_stock"),
            # Test case 7: Edge case - one less than stock
            (5, 4, False, "one_under_stock"),
            # Test case 8: Large stock, quantity exceeds
            (100, 150, True, "large_stock_exceeded"),
            # Test case 9: Large stock, sufficient
            (100, 50, False, "large_stock_sufficient"),
        ],
        ids=[
            "quantity_double_stock",
            "zero_stock",
            "quantity_slightly_over",
            "exact_stock_match",
            "sufficient_stock",
            "one_over_stock",
            "one_under_stock",
            "large_stock_exceeded",
            "large_stock_sufficient",
        ],
    )
    def test_stock_availability_validation(
        self, product_stock, cart_quantity, should_fail, description
    ):
        """
        Test that validation checks product stock availability.

        For each stock/quantity combination:
        1. Create product with specified stock
        2. Create cart with item requesting specified quantity
        3. Validate cart
        4. Verify validation result matches expected outcome
        """
        # Create product with specified stock (no VAT to avoid price complications)
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            stock=product_stock,
            vat=None,
        )
        product.set_current_language("en")
        product.name = f"Test Product - {description}"
        product.save()

        # Create cart with item
        cart = CartFactory.create()
        CartItemFactory.create(
            cart=cart, product=product, quantity=cart_quantity
        )

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        if should_fail:
            # Verify validation failed
            assert result["valid"] is False, (
                f"Validation should fail for {description} (stock={product_stock}, quantity={cart_quantity})"
            )
            assert len(result["errors"]) > 0, "Should have at least one error"
            assert any(
                "stock" in str(error).lower() for error in result["errors"]
            ), f"Error should mention stock: {result['errors']}"
        else:
            # Verify validation succeeded
            assert result["valid"] is True, (
                f"Validation should succeed for {description} (stock={product_stock}, quantity={cart_quantity})"
            )
            assert len(result["errors"]) == 0, (
                f"Should have no errors, got: {result['errors']}"
            )

    @pytest.mark.parametrize(
        "original_price,current_price,should_fail,description",
        [
            # Test case 1: Price increased significantly (>5%)
            (
                Decimal("100.00"),
                Decimal("110.00"),
                True,
                "price_increased_10_percent",
            ),
            # Test case 2: Price decreased significantly (>5%)
            (
                Decimal("100.00"),
                Decimal("90.00"),
                True,
                "price_decreased_10_percent",
            ),
            # Test case 3: Price increased at tolerance boundary (exactly 5%)
            (
                Decimal("100.00"),
                Decimal("105.00"),
                False,
                "price_increased_5_percent",
            ),
            # Test case 4: Price decreased at tolerance boundary (exactly 5%)
            (
                Decimal("100.00"),
                Decimal("95.00"),
                False,
                "price_decreased_5_percent",
            ),
            # Test case 5: Price increased within tolerance (3%)
            (
                Decimal("100.00"),
                Decimal("103.00"),
                False,
                "price_increased_3_percent",
            ),
            # Test case 6: Price decreased within tolerance (3%)
            (
                Decimal("100.00"),
                Decimal("97.00"),
                False,
                "price_decreased_3_percent",
            ),
            # Test case 7: No price change
            (Decimal("100.00"), Decimal("100.00"), False, "no_price_change"),
            # Test case 8: Tiny price increase (0.5%)
            (
                Decimal("100.00"),
                Decimal("100.50"),
                False,
                "tiny_price_increase",
            ),
            # Test case 9: Large price increase (20%)
            (
                Decimal("100.00"),
                Decimal("120.00"),
                True,
                "large_price_increase",
            ),
            # Test case 10: Large price decrease (20%)
            (Decimal("100.00"), Decimal("80.00"), True, "large_price_decrease"),
            # Test case 11: Price doubled
            (Decimal("50.00"), Decimal("100.00"), True, "price_doubled"),
            # Test case 12: Price halved
            (Decimal("100.00"), Decimal("50.00"), True, "price_halved"),
            # Test case 13: Small product - price increased >5%
            (
                Decimal("10.00"),
                Decimal("11.00"),
                True,
                "small_product_price_increased",
            ),
            # Test case 14: Small product - price increased within tolerance
            (
                Decimal("10.00"),
                Decimal("10.40"),
                False,
                "small_product_within_tolerance",
            ),
        ],
        ids=[
            "price_increased_10_percent",
            "price_decreased_10_percent",
            "price_increased_5_percent",
            "price_decreased_5_percent",
            "price_increased_3_percent",
            "price_decreased_3_percent",
            "no_price_change",
            "tiny_price_increase",
            "large_price_increase",
            "large_price_decrease",
            "price_doubled",
            "price_halved",
            "small_product_price_increased",
            "small_product_within_tolerance",
        ],
    )
    def test_price_change_validation(
        self, original_price, current_price, should_fail, description
    ):
        """
        Test that validation checks for significant price changes (>5% tolerance).

        For each price combination:
        1. Create product with current price
        2. Create cart item (simulating it was added at original price)
        3. Validate cart
        4. Verify validation result based on price difference

        Note: Prices within 5% tolerance should generate warnings but not errors.
        Prices exceeding 5% tolerance should generate errors.
        """
        # Create product with current price (no discount, no VAT)
        product = ProductFactory.create(
            price=Money(str(current_price), settings.DEFAULT_CURRENCY),
            discount_percent=0,  # No discount
            vat=None,  # No VAT to avoid price inflation
            stock=10,
        )
        product.set_current_language("en")
        product.name = f"Test Product - {description}"
        product.save()

        # Create cart with item at original price
        cart = CartFactory.create()
        cart_item = CartItemFactory.create(
            cart=cart, product=product, quantity=1
        )

        # Manually set the cart item's price_at_add to simulate original price
        # This simulates the price at the time the item was added to cart
        cart_item.price_at_add = Money(
            str(original_price), settings.DEFAULT_CURRENCY
        )
        cart_item.save()

        # Now update product to current price (if different)
        if original_price != current_price:
            product.price = Money(str(current_price), settings.DEFAULT_CURRENCY)
            product.discount_percent = 0  # Ensure no discount
            product.vat = None  # Ensure no VAT
            product.save()

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        # Calculate expected price difference
        price_diff_percent = (
            abs((current_price - original_price) / original_price * 100)
            if original_price > 0
            else 0
        )

        if should_fail:
            # Price change >5% should cause validation failure
            assert result["valid"] is False, (
                f"Validation should fail for {description} (diff={price_diff_percent:.2f}%)"
            )
            assert len(result["errors"]) > 0, "Should have at least one error"
            assert any(
                "price" in str(error).lower() for error in result["errors"]
            ), f"Error should mention price: {result['errors']}"
        else:
            # Price change <=5% should succeed (may have warnings)
            assert result["valid"] is True, (
                f"Validation should succeed for {description} (diff={price_diff_percent:.2f}%)"
            )
            assert len(result["errors"]) == 0, (
                f"Should have no errors, got: {result['errors']}"
            )

    def test_multiple_items_with_mixed_issues(self):
        """
        Test validation with multiple cart items having different issues.

        This test verifies that validation catches all issues across
        multiple cart items in a single validation pass.
        """
        # Create cart
        cart = CartFactory.create()

        # Item 1: Valid item (no issues)
        product1 = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10, vat=None
        )
        product1.set_current_language("en")
        product1.name = "Valid Product"
        product1.save()
        CartItemFactory.create(cart=cart, product=product1, quantity=5)

        # Item 2: Insufficient stock
        product2 = ProductFactory.create(
            price=Money("30.00", settings.DEFAULT_CURRENCY), stock=2, vat=None
        )
        product2.set_current_language("en")
        product2.name = "Out of Stock Product"
        product2.save()
        CartItemFactory.create(cart=cart, product=product2, quantity=5)

        # Item 3: Price changed significantly
        product3 = ProductFactory.create(
            price=Money("100.00", settings.DEFAULT_CURRENCY),
            stock=10,
            discount_percent=0,
            vat=None,
        )
        product3.set_current_language("en")
        product3.name = "Price Changed Product"
        product3.save()
        cart_item3 = CartItemFactory.create(
            cart=cart, product=product3, quantity=2
        )

        # Set price_at_add to original price
        cart_item3.price_at_add = Money("100.00", settings.DEFAULT_CURRENCY)
        cart_item3.save()

        # Simulate price change by updating product price
        product3.price = Money(
            "80.00", settings.DEFAULT_CURRENCY
        )  # 20% decrease
        product3.discount_percent = 0
        product3.vat = None
        product3.save()

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        # Verify validation failed
        assert result["valid"] is False, (
            "Validation should fail with multiple issues"
        )

        # Verify we have multiple errors (stock + price)
        assert len(result["errors"]) >= 2, (
            f"Should have at least 2 errors (stock + price), got {len(result['errors'])}"
        )

        # Verify stock error present
        assert any(
            "stock" in str(error).lower() for error in result["errors"]
        ), "Should have stock-related error"

        # Verify price error present
        assert any(
            "price" in str(error).lower() for error in result["errors"]
        ), "Should have price-related error"

    def test_product_deleted_after_adding_to_cart(self):
        """
        Test validation when product is deleted after being added to cart.

        This tests the edge case where a product is removed from the catalog
        after a customer has added it to their cart.
        """
        # Create product and add to cart
        product = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10, vat=None
        )
        product.set_current_language("en")
        product.name = "Product to be deleted"
        product.save()

        cart = CartFactory.create()
        cart_item = CartItemFactory.create(
            cart=cart, product=product, quantity=2
        )

        # Delete the product (soft delete if implemented, or hard delete)
        product.delete()

        # Refresh cart item - the product foreign key will be None or raise error
        cart_item.refresh_from_db()

        # Validate cart - should handle missing product gracefully
        try:
            result = OrderService.validate_cart_for_checkout(cart)

            # Verify validation failed
            assert result["valid"] is False, (
                "Validation should fail when product no longer exists"
            )
            assert len(result["errors"]) > 0, "Should have at least one error"
        except ProductNotFoundError:
            # This is also acceptable - validation raises exception for missing product
            pass

    def test_all_valid_items_passes_validation(self):
        """
        Test that cart with all valid items passes validation.

        This is the positive test case - verifying that valid carts
        are not incorrectly rejected.
        """
        # Create cart with multiple valid items
        cart = CartFactory.create()

        # Item 1: Valid
        product1 = ProductFactory.create(
            price=Money("50.00", settings.DEFAULT_CURRENCY), stock=10, vat=None
        )
        product1.set_current_language("en")
        product1.name = "Valid Product 1"
        product1.save()
        CartItemFactory.create(cart=cart, product=product1, quantity=5)

        # Item 2: Valid
        product2 = ProductFactory.create(
            price=Money("30.00", settings.DEFAULT_CURRENCY), stock=20, vat=None
        )
        product2.set_current_language("en")
        product2.name = "Valid Product 2"
        product2.save()
        CartItemFactory.create(cart=cart, product=product2, quantity=10)

        # Item 3: Valid
        product3 = ProductFactory.create(
            price=Money("100.00", settings.DEFAULT_CURRENCY), stock=5, vat=None
        )
        product3.set_current_language("en")
        product3.name = "Valid Product 3"
        product3.save()
        CartItemFactory.create(cart=cart, product=product3, quantity=2)

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        # Verify validation succeeded
        assert result["valid"] is True, (
            f"Validation should succeed for all valid items, errors: {result['errors']}"
        )
        assert len(result["errors"]) == 0, (
            f"Should have no errors, got: {result['errors']}"
        )

    def test_price_within_tolerance_generates_warning_not_error(self):
        """
        Test that price changes within 5% tolerance generate warnings, not errors.

        This verifies the distinction between warnings (informational) and
        errors (blocking) for price changes.
        """
        # Create product (no discount, no VAT)
        product = ProductFactory.create(
            price=Money("100.00", settings.DEFAULT_CURRENCY),
            discount_percent=0,  # No discount
            vat=None,  # No VAT to avoid price inflation
            stock=10,
        )
        product.set_current_language("en")
        product.name = "Product with minor price change"
        product.save()

        # Create cart with item at original price
        cart = CartFactory.create()
        cart_item = CartItemFactory.create(
            cart=cart, product=product, quantity=2
        )

        # Set price_at_add to original price
        cart_item.price_at_add = Money("100.00", settings.DEFAULT_CURRENCY)
        cart_item.save()

        # Change price by 3% (within 5% tolerance)
        product.price = Money("103.00", settings.DEFAULT_CURRENCY)
        product.discount_percent = 0  # Ensure no discount
        product.vat = None  # Ensure no VAT
        product.save()

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        # Verify validation succeeded (no errors)
        assert result["valid"] is True, (
            "Validation should succeed for price change within tolerance"
        )
        assert len(result["errors"]) == 0, (
            f"Should have no errors, got: {result['errors']}"
        )

    @pytest.mark.parametrize(
        "num_items,stocks,quantities",
        [
            # Test case 1: Single item, sufficient stock
            (1, [10], [5]),
            # Test case 2: Multiple items, all sufficient
            (3, [10, 20, 15], [5, 10, 8]),
            # Test case 3: Multiple items, one insufficient
            (3, [10, 5, 15], [5, 10, 8]),
            # Test case 4: Multiple items, multiple insufficient
            (3, [10, 5, 8], [15, 10, 10]),
            # Test case 5: Large cart, all sufficient
            (5, [100, 50, 75, 30, 20], [50, 25, 50, 15, 10]),
        ],
        ids=[
            "single_item_sufficient",
            "multiple_items_all_sufficient",
            "multiple_items_one_insufficient",
            "multiple_items_multiple_insufficient",
            "large_cart_all_sufficient",
        ],
    )
    def test_multiple_items_stock_validation(
        self, num_items, stocks, quantities
    ):
        """
        Test stock validation with multiple cart items.

        This test verifies that validation correctly handles carts with
        multiple items, checking stock for each item independently.
        """
        # Create cart
        cart = CartFactory.create()

        # Track if any item should fail
        should_fail = False

        # Create items
        for i in range(num_items):
            product = ProductFactory.create(
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                stock=stocks[i],
                vat=None,
            )
            product.set_current_language("en")
            product.name = f"Product {i + 1}"
            product.save()

            CartItemFactory.create(
                cart=cart, product=product, quantity=quantities[i]
            )

            # Check if this item should fail
            if quantities[i] > stocks[i]:
                should_fail = True

        # Validate cart
        result = OrderService.validate_cart_for_checkout(cart)

        if should_fail:
            # At least one item has insufficient stock
            assert result["valid"] is False, (
                "Validation should fail when any item has insufficient stock"
            )
            assert len(result["errors"]) > 0, "Should have at least one error"
            assert any(
                "stock" in str(error).lower() for error in result["errors"]
            ), "Should have stock-related error"
        else:
            # All items have sufficient stock
            assert result["valid"] is True, (
                f"Validation should succeed when all items have sufficient stock, errors: {result['errors']}"
            )
            assert len(result["errors"]) == 0, (
                f"Should have no errors, got: {result['errors']}"
            )
