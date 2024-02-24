from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from authentication.serializers import AuthenticationSerializer
from core.api.serializers import BaseExpandSerializer
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import ProductSerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class TestBaseExpandSerializer(TestCase):
    user: User = None
    product: Product = None
    instance: ProductFavourite = None
    serializer: BaseExpandSerializer = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.product = Product.objects.create(
            product_code="P123",
            name="Sample Product",
            slug="sample-product",
            price=100.0,
            active=True,
            stock=10,
        )
        for language in languages:
            self.product.set_current_language(language)
            self.product.name = f"Sample Product ({language})"
            self.product.description = f"Sample Product Description ({language})"
            self.product.save()
        self.product.set_current_language(default_language)

        self.instance = ProductFavourite.objects.create(
            user=self.user, product=self.product
        )
        self.serializer = BaseExpandSerializer(instance=self.instance)
        self.serializer.Meta.model = ProductFavourite

    def test_to_representation_without_expansion(self):
        self.serializer.context["expand"] = False
        data = self.serializer.to_representation(self.instance)

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
        data = self.serializer.to_representation(self.instance)

        self.assertIn("user", data)
        self.assertIn("product", data)
        self.assertEqual(data["user"], self.user.id)
        self.assertEqual(data["product"], self.product.id)

    def test_recursive_expansion_prevention(self):
        self.serializer.context["expand"] = True
        self.serializer.expansion_path.append("User")
        data = self.serializer.to_representation(self.instance)

        self.assertIn("user", data)
        self.assertNotIsInstance(data["user"], dict)

    def test_expand_fields_empty(self):
        self.serializer.get_expand_fields = lambda: {}
        self.serializer.context["expand"] = True
        data = self.serializer.to_representation(self.instance)

        self.assertIn("user", data)
        self.assertIn("product", data)
        self.assertEqual(data["user"], self.user.id)
        self.assertEqual(data["product"], self.product.id)

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.product.delete()
        self.instance.delete()
