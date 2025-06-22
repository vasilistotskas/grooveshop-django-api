import json
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.enum.category import CategoryImageTypeEnum
from product.factories.category import ProductCategoryFactory
from product.factories.category_image import ProductCategoryImageFactory
from product.models.category_image import ProductCategoryImage
from product.serializers.category_image import (
    ProductCategoryImageDetailSerializer,
)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductCategoryImageViewSetTestCase(APITestCase):
    def setUp(self):
        self.category = ProductCategoryFactory()
        self.category_image = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.MAIN,
        )
        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )

    def get_product_category_image_detail_url(self, pk):
        return reverse("product-category-image-detail", args=[pk])

    def get_product_category_image_list_url(self):
        return reverse("product-category-image-list")

    def _create_mock_image(self):
        image_path = os.path.join(settings.STATIC_ROOT, "images", "default.png")

        if not os.path.exists(image_path):
            image_path = os.path.join(
                settings.BASE_DIR, "static", "images", "default.png"
            )

        try:
            with open(image_path, "rb") as f:
                image_data = f.read()
        except FileNotFoundError:
            image_data = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13"
                b"\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDATx\x9cc```"
                b"\x00\x00\x00\x04\x00\x01]\xcc[\x1a\x00\x00\x00\x00IEND\xaeB`\x82"
            )

        image_file = SimpleUploadedFile(
            name="test_image.png", content=image_data, content_type="image/png"
        )
        return image_file

    def test_list_uses_correct_serializer(self):
        url = self.get_product_category_image_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            image_data = response.data["results"][0]
            expected_fields = {
                "id",
                "category",
                "category_name",
                "image",
                "image_type",
                "active",
                "sort_order",
                "translations",
                "image_path",
                "image_url",
                "created_at",
                "updated_at",
                "uuid",
            }
            self.assertTrue(expected_fields.issubset(set(image_data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_category_image_detail_url(self.category_image.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "category",
            "category_name",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        payload = {
            "category": self.category.id,
            "image": self._create_mock_image(),
            "image_type": CategoryImageTypeEnum.BANNER,
            "active": True,
            "sort_order": 1,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Test Banner Image",
                        "alt_text": "Test alt text",
                    },
                }
            ),
        }

        url = self.get_product_category_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "category",
            "category_name",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        image = ProductCategoryImage.objects.get(id=response.data["id"])
        self.assertEqual(image.category.id, self.category.id)
        self.assertEqual(image.image_type, CategoryImageTypeEnum.BANNER)

    def test_update_request_response_serializers(self):
        payload = {
            "category": self.category.id,
            "image": self._create_mock_image(),
            "image_type": CategoryImageTypeEnum.ICON,
            "active": True,
            "sort_order": 2,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Updated Icon Image",
                        "alt_text": "Updated alt text",
                    },
                }
            ),
        }

        url = self.get_product_category_image_detail_url(self.category_image.id)
        response = self.client.put(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "category",
            "category_name",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        image = ProductCategoryImage.objects.get(id=response.data["id"])
        self.assertEqual(image.image_type, CategoryImageTypeEnum.ICON)

    def test_partial_update_request_response_serializers(self):
        payload = {
            "active": False,
        }

        url = self.get_product_category_image_detail_url(self.category_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "category",
            "category_name",
            "image",
            "image_type",
            "active",
            "sort_order",
            "translations",
            "image_path",
            "image_url",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        image = ProductCategoryImage.objects.get(id=response.data["id"])
        self.assertEqual(image.active, False)
        self.assertEqual(response.data["active"], False)

    def test_filtering_functionality(self):
        banner_image = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        )
        inactive_image = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.ICON,
            active=False,
        )

        url = self.get_product_category_image_list_url()

        response = self.client.get(url, {"category": self.category.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = [img["id"] for img in response.data["results"]]
        self.assertIn(self.category_image.id, image_ids)
        self.assertIn(banner_image.id, image_ids)
        self.assertIn(inactive_image.id, image_ids)

        response = self.client.get(
            url, {"image_type": CategoryImageTypeEnum.BANNER}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], banner_image.id)

        response = self.client.get(url, {"active": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        active_ids = [img["id"] for img in response.data["results"]]
        self.assertIn(self.category_image.id, active_ids)
        self.assertIn(banner_image.id, active_ids)
        self.assertNotIn(inactive_image.id, active_ids)

    def test_search_functionality(self):
        searchable_image = ProductCategoryImageFactory(
            category=self.category, image_type=CategoryImageTypeEnum.BANNER
        )
        searchable_image.set_current_language(default_language)
        searchable_image.title = "Searchable Banner Title"
        searchable_image.alt_text = "Unique alt text content"
        searchable_image.save()

        url = self.get_product_category_image_list_url()

        response = self.client.get(url, {"search": "Searchable"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = [img["id"] for img in response.data["results"]]
        self.assertIn(searchable_image.id, image_ids)

        response = self.client.get(url, {"search": "Unique"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = [img["id"] for img in response.data["results"]]
        self.assertIn(searchable_image.id, image_ids)

    def test_ordering_functionality(self):
        url = self.get_product_category_image_list_url()

        response = self.client.get(url, {"ordering": "sort_order"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "-created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if len(response.data["results"]) > 1:
            first_created = response.data["results"][0]["created_at"]
            second_created = response.data["results"][1]["created_at"]
            self.assertGreaterEqual(first_created, second_created)

    def test_validation_errors_consistent(self):
        payload = {
            "category": self.category.id,
            "image": self._create_mock_image(),
            "image_type": CategoryImageTypeEnum.MAIN,
            "active": True,
            "sort_order": 1,
            "translations": json.dumps(
                {
                    default_language: {
                        "title": "Duplicate Main Image",
                        "alt_text": "This should fail",
                    },
                }
            ),
        }

        url = self.get_product_category_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_product_category_image_detail_url(self.category_image.id)
        viewset_response = self.client.get(url)

        manual_serializer = ProductCategoryImageDetailSerializer(
            self.category_image
        )

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        key_fields = ["id", "category", "image_type", "active"]
        for field in key_fields:
            self.assertEqual(
                viewset_response.data[field], manual_serializer.data[field]
            )

    def test_bulk_update_functionality(self):
        image1 = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        )
        image2 = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.ICON,
            active=True,
        )

        payload = {
            "image_ids": [image1.id, image2.id],
            "active": False,
            "sort_order": 10,
        }

        url = reverse("product-category-image-bulk-update")
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("success", response.data)
        self.assertIn("updated_count", response.data)
        self.assertEqual(response.data["updated_count"], 2)

        image1.refresh_from_db()
        image2.refresh_from_db()
        self.assertEqual(image1.active, False)
        self.assertEqual(image2.active, False)
        self.assertEqual(image1.sort_order, 10)
        self.assertEqual(image2.sort_order, 10)

    def test_by_category_action(self):
        other_category = ProductCategoryFactory()
        other_image = ProductCategoryImageFactory(category=other_category)

        url = reverse("product-category-image-by-category")
        response = self.client.get(url, {"category_id": self.category.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = [img["id"] for img in response.data]
        self.assertIn(self.category_image.id, image_ids)
        self.assertNotIn(other_image.id, image_ids)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_by_type_action(self):
        banner_image = ProductCategoryImageFactory(
            category=self.category,
            image_type=CategoryImageTypeEnum.BANNER,
        )

        url = reverse("product-category-image-by-type")
        response = self.client.get(
            url, {"image_type": CategoryImageTypeEnum.BANNER}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], banner_image.id)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_complex_payload(self):
        payload = {
            "category": self.category.id,
            "image": self._create_mock_image(),
            "image_type": CategoryImageTypeEnum.THUMBNAIL,
            "active": True,
            "sort_order": 3,
            "translations": json.dumps(
                {
                    "en": {
                        "title": "Complex Thumbnail EN",
                        "alt_text": "Complex thumbnail alt text EN",
                    },
                    "de": {
                        "title": "Complex Thumbnail DE",
                        "alt_text": "Complex thumbnail alt text DE",
                    },
                }
            ),
        }

        url = self.get_product_category_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        image = ProductCategoryImage.objects.get(id=response.data["id"])

        if "en" in [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]:
            image.set_current_language("en")
            self.assertEqual(image.title, "Complex Thumbnail EN")

        if "de" in [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]:
            image.set_current_language("de")
            self.assertEqual(image.title, "Complex Thumbnail DE")

    def test_queryset_optimization(self):
        url = self.get_product_category_image_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_image_type_enum_values(self):
        valid_types = [
            CategoryImageTypeEnum.MAIN,
            CategoryImageTypeEnum.BANNER,
            CategoryImageTypeEnum.ICON,
            CategoryImageTypeEnum.THUMBNAIL,
        ]

        for image_type in valid_types:
            if image_type != CategoryImageTypeEnum.MAIN:
                payload = {
                    "category": self.category.id,
                    "image": self._create_mock_image(),
                    "image_type": image_type,
                    "active": True,
                    "sort_order": 1,
                    "translations": json.dumps(
                        {
                            default_language: {
                                "title": f"Test {image_type} Image",
                                "alt_text": f"Alt text for {image_type}",
                            },
                        }
                    ),
                }

                url = self.get_product_category_image_list_url()
                response = self.client.post(
                    url, data=payload, format="multipart"
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response.data["image_type"], image_type)
