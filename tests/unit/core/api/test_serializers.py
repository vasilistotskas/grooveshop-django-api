import pytz
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.api.serializers import BaseExpandSerializer
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.product import ProductSerializer
from user.serializers.account import UserAccountSerializer

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

    def test_to_representation_without_expansion(self):
        data = self.serializer.to_representation(self.instance)

        timezone_to_use = settings.TIME_ZONE
        athens_timezone = pytz.timezone(timezone_to_use)
        expected_created_at = self.instance.created_at.astimezone(
            athens_timezone
        ).isoformat()
        expected_updated_at = self.instance.updated_at.astimezone(
            athens_timezone
        ).isoformat()

        expected_data = {
            "user": self.user.id,
            "product": self.product.id,
            "id": self.instance.id,
            "created_at": expected_created_at,
            "updated_at": expected_updated_at,
            "uuid": str(self.instance.uuid),
        }
        self.assertEqual(data, expected_data)

    def test_to_representation_with_expansion_single_field(self):
        self.serializer.Meta.model = ProductFavourite
        expand_fields = {
            "product": ProductSerializer,
        }
        self.serializer.context["expand"] = True
        self.serializer.get_expand_fields = lambda: expand_fields

        related_instance = ProductSerializer().to_representation(self.product)

        data = self.serializer.to_representation(self.instance)

        timezone_to_use = settings.TIME_ZONE
        athens_timezone = pytz.timezone(timezone_to_use)
        expected_created_at = self.instance.created_at.astimezone(
            athens_timezone
        ).isoformat()
        expected_updated_at = self.instance.updated_at.astimezone(
            athens_timezone
        ).isoformat()

        expected_data = {
            "product": related_instance,
            "user": self.user.id,
            "id": self.instance.id,
            "created_at": expected_created_at,
            "updated_at": expected_updated_at,
            "uuid": str(self.instance.uuid),
        }

        self.assertEqual(data, expected_data)

    def test_to_representation_with_expansion_multiple_fields(self):
        self.serializer.Meta.model = ProductFavourite
        expand_fields = {
            "user": UserAccountSerializer,
            "product": ProductSerializer,
        }
        self.serializer.context["expand"] = True
        self.serializer.get_expand_fields = lambda: expand_fields

        user_instance = UserAccountSerializer().to_representation(self.user)
        product_instance = ProductSerializer().to_representation(self.product)

        data = self.serializer.to_representation(self.instance)

        timezone_to_use = settings.TIME_ZONE
        athens_timezone = pytz.timezone(timezone_to_use)
        expected_created_at = self.instance.created_at.astimezone(
            athens_timezone
        ).isoformat()
        expected_updated_at = self.instance.updated_at.astimezone(
            athens_timezone
        ).isoformat()

        expected_data = {
            "user": user_instance,
            "product": product_instance,
            "id": self.instance.id,
            "created_at": expected_created_at,
            "updated_at": expected_updated_at,
            "uuid": str(self.instance.uuid),
        }

        self.assertEqual(data, expected_data)

    def test_get_expand_fields(self):
        self.serializer.Meta.model = ProductFavourite
        expand_fields = {
            "product": ProductSerializer,
        }
        self.serializer.get_expand_fields = lambda: expand_fields

        result = self.serializer.get_expand_fields()
        self.assertEqual(result, expand_fields)

    def test_expand_serializer_list_field(self):
        self.serializer.Meta.model = ProductFavourite
        expand_fields = {
            "product": ProductSerializer,
        }
        self.serializer.context["expand"] = True
        self.serializer.get_expand_fields = lambda: expand_fields

        related_instance = ProductSerializer().to_representation(self.product)

        data = self.serializer.to_representation(self.instance)

        timezone_to_use = settings.TIME_ZONE
        athens_timezone = pytz.timezone(timezone_to_use)
        expected_created_at = self.instance.created_at.astimezone(
            athens_timezone
        ).isoformat()
        expected_updated_at = self.instance.updated_at.astimezone(
            athens_timezone
        ).isoformat()

        expected_data = {
            "product": related_instance,
            "user": self.user.id,
            "id": self.instance.id,
            "created_at": expected_created_at,
            "updated_at": expected_updated_at,
            "uuid": str(self.instance.uuid),
        }

        self.assertEqual(data, expected_data)

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.product.delete()
        self.instance.delete()
