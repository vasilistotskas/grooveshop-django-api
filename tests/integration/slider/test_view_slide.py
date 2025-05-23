from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils.timezone import now
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from slider.factories import SlideFactory, SliderFactory
from slider.models import Slide, Slider

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SlideViewSetTestCase(TestURLFixerMixin, APITestCase):
    slide: Slide = None
    slider: Slider = None

    def setUp(self):
        date_start = now()
        self.slider = SliderFactory(num_slides=0)
        self.slide = SlideFactory(
            discount=0.0,
            show_button=True,
            date_start=date_start,
            date_end=date_start + timedelta(days=30),
            slider=self.slider,
        )

    @staticmethod
    def get_slide_detail_url(pk):
        return reverse("slide-detail", kwargs={"pk": pk})

    @staticmethod
    def get_slide_list_url():
        return reverse("slide-list")

    def test_list(self):
        url = self.get_slide_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if isinstance(response.data, dict) and "results" in response.data:
            slides = Slide.objects.all()
            self.assertEqual(len(response.data["results"]), slides.count())
        else:
            slides = Slide.objects.all()
            self.assertEqual(len(response.data), slides.count())

    def test_create_valid(self):
        date_start = now()
        payload = {
            "slider": self.slider.pk,
            "discount": 0.0,
            "show_button": True,
            "date_start": date_start,
            "date_end": date_start + timedelta(days=30),
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Slide name in {language_name}",
                "url": "https://www.example.com/",
                "title": f"New Slide title in {language_name}",
                "description": f"New Slide description in {language_name}",
                "button_label": f"New btn in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slide_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "discount": "invalid_discount",
            "show_button": "invalid_show_button",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_slide_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("id", response.data)
        self.assertEqual(response.data["id"], self.slide.id)
        self.assertIn("translations", response.data)
        self.assertIn("created_at", response.data)
        self.assertIn("updated_at", response.data)
        self.assertIn("slider", response.data)

    def test_retrieve_invalid(self):
        invalid_slide_id = 9999
        url = self.get_slide_detail_url(invalid_slide_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        date_start = now()

        payload = {
            "slider": self.slider.pk,
            "discount": 10.0,
            "show_button": False,
            "date_start": date_start,
            "date_end": date_start + timedelta(days=30),
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Slide name in {language_name}",
                "url": "https://www.example.com/",
                "title": f"Updated Slide title in {language_name}",
                "description": f"Updated Slide description in {language_name}",
                "button_label": f"Updated btn in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "discount": "invalid_discount",
            "show_button": "invalid_show_button",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }
        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "discount": 12.0,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Slide name in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "discount": "invalid_discount",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }
        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_slide_detail_url(self.slide.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Slide.objects.filter(pk=self.slide.pk).exists())

    def test_destroy_invalid(self):
        invalid_slide_id = 9999
        url = self.get_slide_detail_url(invalid_slide_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
