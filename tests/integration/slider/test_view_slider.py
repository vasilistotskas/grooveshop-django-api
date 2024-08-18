from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.tests import compare_serializer_and_response
from slider.factories import SliderFactory
from slider.models import Slider
from slider.serializers import SliderSerializer


languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SliderViewSetTestCase(APITestCase):
    slider: Slider = None

    def setUp(self):
        self.slider = SliderFactory(num_slides=0)

    @staticmethod
    def get_slider_detail_url(pk):
        return reverse("slider-detail", args=[pk])

    @staticmethod
    def get_slider_list_url():
        return reverse("slider-list")

    def test_list(self):
        url = self.get_slider_list_url()
        response = self.client.get(url)
        sliders = Slider.objects.all()
        serializer = SliderSerializer(sliders, many=True)
        for response_item, serializer_item in zip(response.data["results"], serializer.data):
            compare_serializer_and_response(serializer_item, response_item, ["video", "thumbnail"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Slider in {language_name}",
                "url": "https://www.example.com/",
                "title": f"New Slider Title in {language_name}",
                "description": f"New Slider Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slider_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "image": "invalid_image",
            "video": "invalid_video",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                    "title": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
        }

        url = self.get_slider_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.get(url)
        slider = Slider.objects.get(pk=self.slider.pk)
        serializer = SliderSerializer(slider)
        compare_serializer_and_response(serializer.data, response.data, ["video", "thumbnail"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_slider_id = 9999
        url = self.get_slider_detail_url(invalid_slider_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Slider in {language_name}",
                "url": "https://www.example.com/",
                "title": f"Updated Slider Title in {language_name}",
                "description": f"Updated Slider Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "image": "invalid_image",
            "video": "invalid_video",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                    "title": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
        }

        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Slider in {language_name}",
                "url": "https://www.example.com/",
                "title": f"Updated Slider Title in {language_name}",
                "description": f"Updated Slider Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "video": "invalid_video",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "url": "Translation for invalid language code",
                    "title": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                }
            },
        }

        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_slider_detail_url(self.slider.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Slider.objects.filter(pk=self.slider.pk).exists())

    def test_destroy_invalid(self):
        invalid_slider_id = 9999
        url = self.get_slider_detail_url(invalid_slider_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        Slider.objects.all().delete()
        super().tearDown()
