from django.contrib.auth import get_user_model
from django.test import TestCase

from product.models.favourite import ProductFavourite
from product.models.product import Product


User = get_user_model()


class ProductFavouriteModelTestCase(TestCase):
    user: User = None
    product: Product = None

    def setUp(self):
        self.user = User.objects.create_user(email="test@test.com", password="test12345@!")
        self.product = Product.objects.create(
            product_code="P123",
            name="Sample Product",
            slug="sample-product",
            price=100.0,
            active=True,
            stock=10,
        )

    def test_fields(self):
        favourite = ProductFavourite.objects.create(user=self.user, product=self.product)
        self.assertIsNotNone(favourite.id)
        self.assertEqual(favourite.user, self.user)
        self.assertEqual(favourite.product, self.product)

    def test_str_representation(self):
        favourite = ProductFavourite.objects.create(user=self.user, product=self.product)
        self.assertEqual(str(favourite), f"{self.user.email} - {self.product.name}")

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.product.delete()
