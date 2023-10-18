from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cart.models import Cart
from cart.models import CartItem
from product.models.product import Product

User = get_user_model()


class CartModelTestCase(TestCase):
    cart: Cart = None
    user: User = None
    cart_item_1: CartItem = None
    cart_item_2: CartItem = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.cart = Cart.objects.create(user=self.user)
        product_1 = Product.objects.create(
            slug="product_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )
        product_2 = Product.objects.create(
            slug="product_two",
            price=25.00,
            active=True,
            stock=10,
            discount_percent=10.00,
            hits=0,
            weight=0.00,
        )
        self.cart_item_1 = CartItem.objects.create(
            cart=self.cart, product=product_1, quantity=2
        )
        self.cart_item_2 = CartItem.objects.create(
            cart=self.cart, product=product_2, quantity=3
        )

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.cart.user, self.user)
        self.assertEqual(self.cart.last_activity.date(), timezone.now().date())

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(Cart._meta.verbose_name, "Cart")
        self.assertEqual(Cart._meta.verbose_name_plural, "Carts")

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(Cart._meta.verbose_name, "Cart")
        self.assertEqual(Cart._meta.verbose_name_plural, "Carts")

    def test_str_representation(self):
        # Test the __str__ method returns the cart user - cart id
        expected_str = f"Cart {self.user} - {self.cart.id}"
        self.assertEqual(str(self.cart), expected_str)

    def test_get_items(self):
        self.assertEqual(self.cart.get_items().count(), 2)

    def test_total_price(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_price = (
            self.cart_item_1.total_price.amount + self.cart_item_2.total_price.amount
        )
        self.assertEqual(self.cart.total_price.amount, expected_total_price)

    def test_total_discount_value(self):
        self.cart_item_1.refresh_from_db()
        self.cart_item_2.refresh_from_db()

        expected_total_discount = (
            self.cart_item_1.total_discount_value.amount
            + self.cart_item_2.total_discount_value.amount
        )
        self.assertEqual(self.cart.total_discount_value.amount, expected_total_discount)

    def test_total_vat_value(self):
        expected_total_vat = (
            self.cart_item_1.product.vat_value + self.cart_item_2.product.vat_value
        )
        self.assertEqual(self.cart.total_vat_value, expected_total_vat)

    def test_total_items(self):
        expected_total_items = self.cart_item_1.quantity + self.cart_item_2.quantity
        self.assertEqual(self.cart.total_items, expected_total_items)

    def test_total_items_unique(self):
        self.assertEqual(self.cart.total_items_unique, 2)

    def test_refresh_last_activity(self):
        last_activity_before_refresh = self.cart.last_activity
        self.cart.refresh_last_activity()
        self.assertNotEqual(self.cart.last_activity, last_activity_before_refresh)

    def tearDown(self) -> None:
        super().tearDown()
        self.cart.delete()
        self.user.delete()
        self.cart_item_1.delete()
        self.cart_item_2.delete()
