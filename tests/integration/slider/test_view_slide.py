from __future__ import annotations

import json

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
        self.slider = Slider.objects.create(
            name="test",
            url="http://localhost:8000/",
            title="test",
            description="test",
        )

        self.slide = Slide.objects.create(
            slider=self.slider,
            url="https://www.youtube.com/watch?v=1",
            title="title",
            subtitle="subtitle",
            description="description",
            button_label="test",
            show_button=True,
            order_position=1,
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
        slider = Slider.objects.create(
            name="test_new",
            url="http://localhost:8000/",
            title="test_new",
            description="test_new",
        )
        payload = {
            "slider": slider.pk,
            "url": "https://www.youtube.com/watch?v=1",
            "title": "title",
            "subtitle": "subtitle",
            "description": "description",
            "discount": 10,
            "button_label": "button",
            "show_button": True,
            "order_position": 1,
            "date_start": str(now()),
            "date_end": str(now()),
        }
        response = self.client.post(
            "/api/v1/slide/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slider": self.slider.pk,
            "url": True,
            "title": True,
            "subtitle": True,
            "description": True,
            "discount": "INVALID",
            "button_label": True,
            "show_button": "INVALID",
            "order_position": "INVALID",
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
            "slider": self.slider.pk,
            "url": "https://www.youtube.com/watch?v=1",
            "title": "title",
            "subtitle": "subtitle",
            "description": "description",
            "discount": 10,
            "button_label": "button",
            "show_button": True,
            "order_position": 1,
            "date_end": str(now()),
            "date_start": str(now()),
        }
        response = self.client.put(
            f"/api/v1/slide/{self.slide.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slider": self.slider.pk,
            "url": True,
            "title": True,
            "subtitle": True,
            "description": True,
            "discount": "INVALID",
            "button_label": True,
            "show_button": "INVALID",
            "order_position": "INVALID",
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
            "url": "https://www.youtube.com/watch?v=1",
            "title": "title",
            "subtitle": "subtitle",
            "description": "description",
            "discount": 10,
            "button_label": "button",
            "show_button": True,
            "order_position": 1,
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
            "url": True,
            "title": True,
            "subtitle": True,
            "description": True,
            "discount": "INVALID",
            "button_label": True,
            "show_button": "INVALID",
            "order_position": "INVALID",
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
