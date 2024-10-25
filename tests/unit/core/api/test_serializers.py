from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from authentication.serializers import AuthenticationSerializer
from core.api.serializers import BaseExpandSerializer
from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import ProductSerializer
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class TestBaseExpandSerializer(TestCase):
    user: User = None
    product: Product = None
    product_favourite: ProductFavourite = None
    serializer: BaseExpandSerializer = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_favourite = ProductFavouriteFactory(user=self.user, product=self.product)
        self.serializer = BaseExpandSerializer(instance=self.product_favourite)
        self.serializer.Meta.model = ProductFavourite

    def test_to_representation_without_expansion(self):
        self.serializer.context["expand"] = False
        data = self.serializer.to_representation(self.product_favourite)

        self.assertIn("user", data)
        self.assertIn("product", data)
        self.assertEqual(data["user"], self.user.id)
        self.assertEqual(data["product"], self.product.id)

    def test_to_representation_with_expansion(self):
        expand_fields = {
            "user": AuthenticationSerializer,
            "product": ProductSerializer,
        }
        self.serializer.get_expand_fields = lambda: expand_fields
        self.serializer.context["expand"] = True
        data = self.serializer.to_representation(self.product_favourite)

        self.assertIn("user", data)
        self.assertIn("product", data)
        self.assertEqual(data["user"], self.user.id)
        self.assertEqual(data["product"], self.product.id)

    def test_recursive_expansion_prevention(self):
        self.serializer.context["expand"] = True
        self.serializer.expansion_path.append("User")
        data = self.serializer.to_representation(self.product_favourite)

        self.assertIn("user", data)
        self.assertNotIsInstance(data["user"], dict)

    def test_expand_fields_empty(self):
        self.serializer.get_expand_fields = lambda: {}
        self.serializer.context["expand"] = True
        data = self.serializer.to_representation(self.product_favourite)

        self.assertIn("user", data)
        self.assertIn("product", data)
        self.assertEqual(data["user"], self.user.id)
        self.assertEqual(data["product"], self.product.id)
