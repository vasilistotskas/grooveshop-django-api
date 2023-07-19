from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from slider.models import Slider
from slider.serializers import SliderSerializer


class SliderViewSetTestCase(APITestCase):
    slider: Slider

    def setUp(self):
        self.slider = Slider.objects.create()

    def test_list(self):
        response = self.client.get("/api/v1/slider/")
        sliders = Slider.objects.all()
        serializer = SliderSerializer(sliders, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "translations": {},
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "url": "https://www.youtube.com/watch?v=1",
                "title": f"Title for {language_name}",
                "description": f"Description for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/slider/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "url": "http://localhost:8000/",
        }
        response = self.client.post(
            "/api/v1/slider/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/slider/{self.slider.pk}/")
        slider = Slider.objects.get(pk=self.slider.pk)
        serializer = SliderSerializer(slider)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_slider = "invalid"
        response = self.client.get(f"/api/v1/slider/{invalid_slider}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Translation for {language_name}",
                "url": "https://www.youtube.com/watch?v=1",
                "title": f"Title for {language_name}",
                "description": f"Description for {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/slider/{self.slider.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "title": "INVALID INVALID INVALID INVALID INVALID INVALID INVALID INVALID "
            "INVALID INVALID INVALID INVALID INVALID INVALID "
            "INVALID INVALID INVALID INVALID",
            "url": "http://localhost:8000/",
            "image": "test",
        }
        response = self.client.put(
            f"/api/v1/slider/{self.slider.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "url": "http://localhost:8000/",
            "title": "test_three",
            "description": "test_three",
        }
        response = self.client.patch(
            f"/api/v1/slider/{self.slider.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "translations": {},
        }
        response = self.client.patch(
            f"/api/v1/slider/{self.slider.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/slider/{self.slider.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_slider = "invalid"
        response = self.client.delete(f"/api/v1/slider/{invalid_slider}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
