from __future__ import annotations

import json

from django.conf import settings
from django.utils.timezone import now
from rest_framework import status
from rest_framework.test import APITestCase

from slider.models import Slide
from slider.models import Slider
from slider.serializers import SlideSerializer


class SlideViewSetTestCase(APITestCase):
    slide: Slide
    slider: Slider

    def setUp(self):
        self.slider = Slider.objects.create()

        self.slide = Slide.objects.create(
            slider=self.slider,
            show_button=True,
            date_start=now(),
            date_end=now(),
        )

    def test_list(self):
        response = self.client.get("/api/v1/slide/")
        slides = Slide.objects.all()
        serializer = SlideSerializer(slides, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        slider = Slider.objects.create()

        payload = {
            "translations": {},
            "slider": slider.pk,
            "discount": 10,
            "show_button": True,
            "date_start": str(now()),
            "date_end": str(now()),
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "url": "https://www.youtube.com/watch?v=1",
                "title": f"Title for {language_name}",
                "subtitle": f"Subtitle for {language_name}",
                "description": f"Description for {language_name}",
                "button_label": f"Button label for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/slide/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slider": self.slider.pk,
            "discount": "INVALID",
            "show_button": "INVALID",
        }
        response = self.client.post(
            "/api/v1/slide/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/slide/{self.slide.pk}/")
        slide = Slide.objects.get(pk=self.slide.pk)
        serializer = SlideSerializer(slide)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_slide = "invalid"
        response = self.client.get(f"/api/v1/slide/{invalid_slide}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "slider": self.slider.pk,
            "discount": 10,
            "show_button": True,
            "date_end": str(now()),
            "date_start": str(now()),
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "url": "https://www.youtube.com/watch?v=1",
                "title": f"Title for {language_name}",
                "subtitle": f"Subtitle for {language_name}",
                "description": f"Description for {language_name}",
                "button_label": f"Button label for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/slide/{self.slide.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slider": self.slider.pk,
            "discount": "INVALID",
            "show_button": "INVALID",
        }
        response = self.client.put(
            f"/api/v1/slide/{self.slide.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "slider": self.slider.pk,
            "discount": 10,
            "show_button": True,
            "date_end": str(now()),
            "date_start": str(now()),
        }
        response = self.client.patch(
            f"/api/v1/slide/{self.slide.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "slider": self.slider.pk,
            "discount": "INVALID",
            "show_button": "INVALID",
        }
        response = self.client.patch(
            f"/api/v1/slide/{self.slide.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/slide/{self.slide.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_slide = "invalid"
        response = self.client.delete(f"/api/v1/slide/{invalid_slide}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
