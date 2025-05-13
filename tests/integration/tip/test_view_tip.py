from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from tip.enum.tip_enum import TipKindEnum
from tip.factories import TipFactory
from tip.models import Tip

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class TipViewSetTestCase(TestURLFixerMixin, APITestCase):
    tip: Tip = None

    def setUp(self):
        self.tip = TipFactory(
            kind=TipKindEnum.INFO,
            active=True,
        )

    @staticmethod
    def get_tip_detail_url(pk):
        return reverse("tip-detail", kwargs={"pk": pk})

    @staticmethod
    def get_tip_list_url():
        return reverse("tip-list")

    def test_list(self):
        url = self.get_tip_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if isinstance(response.data, dict) and "results" in response.data:
            tips = Tip.objects.all()
            self.assertEqual(len(response.data["results"]), tips.count())
        else:
            tips = Tip.objects.all()
            self.assertEqual(len(response.data), tips.count())

    def test_create_valid(self):
        payload = {
            "kind": TipKindEnum.SUCCESS,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"New Tip in {language_name}",
                "content": f"New Tip content in {language_name}",
                "url": "https://www.google.com",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_tip_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "kind": "invalid_kind",
            "icon": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                    "content": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                }
            },
        }

        url = self.get_tip_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_tip_detail_url(self.tip.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("translations", response.data)
        self.assertIn("icon", response.data)
        self.assertIn("active", response.data)
        self.assertEqual(response.data["active"], self.tip.active)

    def test_retrieve_invalid(self):
        invalid_tip_id = 9999
        url = self.get_tip_detail_url(invalid_tip_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "kind": TipKindEnum.ERROR,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Updated Tip in {language_name}",
                "content": f"Updated Tip content in {language_name}",
                "url": "https://www.google.com",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_tip_detail_url(self.tip.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "kind": "invalid_kind",
            "icon": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                    "content": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                }
            },
        }

        url = self.get_tip_detail_url(self.tip.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "kind": TipKindEnum.ERROR,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Updated Tip in {language_name}",
                "content": f"Updated Tip content in {language_name}",
                "url": "https://www.google.com",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_tip_detail_url(self.tip.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "kind": "invalid_kind",
            "icon": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                    "content": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                }
            },
        }

        url = self.get_tip_detail_url(self.tip.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_tip_detail_url(self.tip.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tip.objects.filter(pk=self.tip.pk).exists())

    def test_destroy_invalid(self):
        invalid_tip_id = 9999
        url = self.get_tip_detail_url(invalid_tip_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
