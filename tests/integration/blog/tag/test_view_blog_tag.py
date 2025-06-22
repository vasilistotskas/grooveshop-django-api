from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.tag import BlogTagFactory
from blog.models.tag import BlogTag
from core.utils.testing import TestURLFixerMixin

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogTagViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tag = BlogTagFactory(active=True)
        cls.inactive_tag = BlogTagFactory(active=False)

    def get_tag_detail_url(self, pk):
        return reverse("blog-tag-detail", args=[pk])

    def get_tag_list_url(self):
        return reverse("blog-tag-list")

    def test_list(self):
        url = self.get_tag_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        if "links" in response.data:
            self.assertIn("next", response.data["links"])
            self.assertIn("previous", response.data["links"])
        else:
            self.assertIn("next", response.data)
            self.assertIn("previous", response.data)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        first_result = response.data["results"][0]
        expected_basic_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(first_result.keys()))
        )

    def test_list_uses_correct_serializer(self):
        url = self.get_tag_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        first_result = response.data["results"][0]
        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(expected_fields.issubset(set(first_result.keys())))

    def test_retrieve_valid(self):
        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        response_fields = set(response.data.keys())

        for field in expected_fields:
            self.assertIn(
                field,
                response_fields,
                f"Detail field '{field}' should be present in detail response",
            )

    def test_retrieve_invalid(self):
        invalid_tag_id = 9999
        url = self.get_tag_detail_url(invalid_tag_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_valid(self):
        payload = {
            "active": True,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "name": f"New Tag name in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_tag_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_invalid(self):
        payload = {
            "active": "invalid_active",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                },
            },
        }

        url = self.get_tag_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_valid(self):
        payload = {
            "active": False,
            "translations": {},
        }

        for language in languages:
            language_code = language
            translation_payload = {
                "name": f"Updated Tag name in {language_code}",
            }
            payload["translations"][language_code] = translation_payload

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_update_invalid(self):
        payload = {
            "active": "invalid_active",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                },
            },
        }

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {"name": "Partially Updated Tag Name"}
            }
        }

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_partial_update_invalid(self):
        payload = {
            "active": "invalid",
            "translations": {default_language: {"name": "Test Tag"}},
        }

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        tag_to_delete = BlogTagFactory(active=True)

        url = self.get_tag_detail_url(tag_to_delete.id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(BlogTag.objects.filter(id=tag_to_delete.id).exists())

    def test_destroy_invalid(self):
        invalid_tag_id = 9999

        url = self.get_tag_detail_url(invalid_tag_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filtering_by_active_status(self):
        url = self.get_tag_list_url()

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for tag_data in response.data["results"]:
            self.assertTrue(tag_data["active"])

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for tag_data in response.data["results"]:
            self.assertFalse(tag_data["active"])

    def test_filtering_by_id(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"id": self.tag.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.tag.id)

    def test_filtering_by_name(self):
        special_tag = BlogTagFactory()
        special_tag.set_current_language(default_language)
        special_tag.name = "SpecialTestTag"
        special_tag.save()

        url = self.get_tag_list_url()
        response = self.client.get(url, {"name": "SpecialTestTag"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        found = False
        for tag_data in response.data["results"]:
            if tag_data["id"] == special_tag.id:
                found = True
                break
        self.assertTrue(found)

    def test_filtering_functionality(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_ordering_functionality(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

        response = self.client.get(url, {"ordering": "id"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_search_functionality(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"search": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_create_request_response_serializers(self):
        payload = {
            "active": True,
            "translations": {
                default_language: {"name": "Test Tag for Serializers"}
            },
        }

        url = self.get_tag_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_detail_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(
            expected_detail_fields.issubset(set(response.data.keys()))
        )

    def test_update_request_response_serializers(self):
        payload = {
            "active": False,
            "translations": {default_language: {"name": "Updated Test Tag"}},
        }

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_detail_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(
            expected_detail_fields.issubset(set(response.data.keys()))
        )

    def test_partial_update_request_response_serializers(self):
        payload = {
            "translations": {
                default_language: {"name": "Partially Updated Test Tag"}
            }
        }

        url = self.get_tag_detail_url(self.tag.id)
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_detail_fields = {
            "id",
            "translations",
            "active",
            "created_at",
            "updated_at",
            "uuid",
            "sort_order",
        }
        self.assertTrue(
            expected_detail_fields.issubset(set(response.data.keys()))
        )

    def test_validation_errors_consistent(self):
        payload = {
            "active": "not_a_boolean",
            "translations": {},
        }

        url = self.get_tag_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertIn("active", response.data)

    def test_ordering_by_sort_order(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"ordering": "sort_order"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_ordering_by_active(self):
        url = self.get_tag_list_url()
        response = self.client.get(url, {"ordering": "active"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
