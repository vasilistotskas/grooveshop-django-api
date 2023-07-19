from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from tip.models import Tip
from tip.serializers import TipSerializer


class TipViewSetTestCase(APITestCase):
    tip: Tip

    def setUp(self):
        self.tip = Tip.objects.create(
            kind="success",
            active=True,
        )

    def test_list(self):
        response = self.client.get("/api/v1/tip/")
        tips = Tip.objects.all()
        serializer = TipSerializer(tips, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "translations": {},
            "kind": "success",
            "active": True,
            "url": "https://www.google.com",
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Title Translation for {language_name}",
                "content": f"Content Translation for {language_name}",
                "url": f"https://www.google.com/{language_code}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/tip/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "kind": "INVALID",
            "active": "INVALID",
        }
        response = self.client.post(
            "/api/v1/tip/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/tip/{self.tip.pk}/")
        tip = Tip.objects.get(pk=self.tip.pk)
        serializer = TipSerializer(tip)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_tip = "invalid"
        response = self.client.get(f"/api/v1/tip/{invalid_tip}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "kind": "error",
            "active": True,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Title Translation for {language_name}",
                "content": f"Content Translation for {language_name}",
                "url": f"https://www.google.com/{language_code}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "kind": "INVALID",
            "active": "INVALID",
        }
        response = self.client.put(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {},
            "kind": "error",
            "active": True,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "title": f"Title Translation for {language_name}",
                "content": f"Content Translation for {language_name}",
                "url": f"https://www.google.com/{language_code}",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.patch(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "kind": "INVALID",
            "active": "INVALID",
        }
        response = self.client.patch(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/tip/{self.tip.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_tip = "invalid"
        response = self.client.delete(f"/api/v1/tip/{invalid_tip}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
