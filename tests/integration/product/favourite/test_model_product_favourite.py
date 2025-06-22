from django.contrib.auth import get_user_model
from django.test import TestCase

from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class ProductFavouriteModelTestCase(TestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)

    def test_fields(self):
        favourite = ProductFavouriteFactory(
            user=self.user, product=self.product
        )
        self.assertIsNotNone(favourite.id)
        self.assertEqual(favourite.user, self.user)
        self.assertEqual(favourite.product, self.product)

    def test_str_representation(self):
        favourite = ProductFavouriteFactory(
            user=self.user, product=self.product
        )
        self.assertEqual(
            str(favourite), f"{self.user.email} - {self.product.name}"
        )
