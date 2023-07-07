from __future__ import annotations

import json

from rest_framework import status
from rest_framework.test import APITestCase

from tip.models import Tip
from tip.serializers import TipSerializer


class TipViewSetTestCase(APITestCase):
    tip: Tip

    def setUp(self):
        self.tip = Tip.objects.create(
            title="title",
            content="content",
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
            "title": "title_two",
            "content": "content_two",
            "kind": "error",
            "active": True,
            "url": "https://www.google.com",
        }
        response = self.client.post(
            "/api/v1/tip/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "title": "INVALID",
            "content": "INVALID",
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
            "title": "title_three",
            "content": "content_three",
            "kind": "error",
            "active": True,
            "url": "https://www.google.com",
        }
        response = self.client.put(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "title": "INVALID",
            "content": "INVALID",
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
            "title": "title_four",
            "content": "content_four",
            "kind": "error",
            "active": True,
        }
        response = self.client.patch(
            f"/api/v1/tip/{self.tip.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "title": "INVALID",
            "content": "INVALID",
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
