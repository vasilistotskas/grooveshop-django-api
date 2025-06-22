import json
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.image import ProductImage
from product.serializers.image import ProductImageDetailSerializer

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductImageViewSetTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )
        self.product = ProductFactory()
        self.product_image = ProductImageFactory(product=self.product)
        self.secondary_image = ProductImageFactory(
            product=self.product, is_main=False, sort_order=2
        )

    def get_product_image_detail_url(self, pk):
        return reverse("product-image-detail", args=[pk])

    def get_product_image_list_url(self):
        return reverse("product-image-list")

    def _create_mock_image(self):
        image = PILImage.new("RGB", (100, 100), color="red")
        file = BytesIO()
        image.save(file, format="JPEG")
        file.seek(0)
        return SimpleUploadedFile(
            "test_image.jpg", file.getvalue(), content_type="image/jpeg"
        )

    def test_list_uses_correct_serializer(self):
        url = self.get_product_image_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            image_data = response.data["results"][0]
            required_fields = ["id", "product", "image", "is_main"]
            for field in required_fields:
                self.assertIn(
                    field,
                    image_data,
                    f"Field '{field}' missing from list response",
                )

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "image",
            "is_main",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

    def test_create_request_response_serializers(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "sort_order": 3,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Test Product Image",
                    },
                }
            ),
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_basic_fields = {
            "id",
            "product",
            "image",
            "is_main",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        actual_fields = set(response.data.keys())
        self.assertTrue(expected_basic_fields.issubset(actual_fields))

        image = ProductImage.objects.get(id=response.data["id"])
        self.assertEqual(image.product.id, self.product.id)
        self.assertEqual(image.is_main, False)
        self.assertEqual(image.sort_order, 3)

    def test_update_request_response_serializers(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": True,
            "sort_order": 1,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Updated Product Image",
                    },
                }
            ),
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.put(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "image",
            "is_main",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        image = ProductImage.objects.get(id=response.data["id"])
        self.assertEqual(image.is_main, True)
        self.assertEqual(image.sort_order, 1)

    def test_partial_update_request_response_serializers(self):
        payload = {
            "is_main": True,
            "sort_order": 1,
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_basic_fields = {
            "id",
            "product",
            "image",
            "is_main",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "translations",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(response.data.keys()))
        )

        image = ProductImage.objects.get(id=response.data["id"])
        self.assertEqual(image.is_main, True)
        self.assertEqual(image.sort_order, 1)

    def test_delete_endpoint(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ProductImage.objects.filter(id=self.product_image.id).exists()
        )

    def test_filtering_functionality(self):
        url = self.get_product_image_list_url()

        response = self.client.get(url, {"product": self.product.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for result in response.data["results"]:
            self.assertEqual(result["product"], self.product.id)

        response = self.client.get(url, {"is_main": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for result in response.data["results"]:
            self.assertTrue(result["is_main"])

    def test_ordering_functionality(self):
        url = self.get_product_image_list_url()

        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "-is_main"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_validation_errors_consistent(self):
        payload = {
            "product": 99999,
            "image": self._create_mock_image(),
            "is_main": "invalid_boolean",
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", response.data)

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        viewset_response = self.client.get(url)

        serializer = ProductImageDetailSerializer(
            self.product_image,
            context={"request": self.client.request().wsgi_request},
        )

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        viewset_data = viewset_response.data
        serializer_data = serializer.data

        key_fields = ["id", "product", "is_main", "sort_order", "uuid"]
        for field in key_fields:
            self.assertEqual(
                viewset_data[field],
                serializer_data[field],
                f"Field '{field}' differs between ViewSet and manual serializer",
            )

    def test_create_with_complex_payload(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "sort_order": 5,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Complex Product Image",
                    },
                }
            ),
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        image = ProductImage.objects.get(id=response.data["id"])
        self.assertEqual(
            image.safe_translation_getter("title"), "Complex Product Image"
        )

    def test_sort_order_auto_assignment(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Auto Sort Order Image",
                    },
                }
            ),
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        image = ProductImage.objects.get(id=response.data["id"])
        self.assertIsNotNone(image.sort_order)
        self.assertGreater(image.sort_order, 0)

    def test_main_image_constraint(self):
        ProductImageFactory(product=self.product, is_main=True)

        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": True,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Another Main Image",
                    },
                }
            ),
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        main_images_count = ProductImage.objects.filter(
            product=self.product, is_main=True
        ).count()
        self.assertGreaterEqual(main_images_count, 1)
        self.assertIn("is_main", response.data)

    def test_image_file_validation(self):
        invalid_file = SimpleUploadedFile(
            "test.txt", b"This is not an image", content_type="text/plain"
        )

        payload = {
            "product": self.product.id,
            "image": invalid_file,
            "is_main": False,
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image", response.data)
