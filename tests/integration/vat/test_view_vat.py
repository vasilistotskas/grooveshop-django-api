from __future__ import annotations

import json

from rest_framework import status
from rest_framework.test import APITestCase

from vat.models import Vat
from vat.serializers import VatSerializer


class VatViewSetTestCase(APITestCase):
    vat: Vat

    def setUp(self):
        self.vat = Vat.objects.create(value=20)

    def test_list(self):
        response = self.client.get("/api/v1/vat/")
        vats = Vat.objects.all()
        serializer = VatSerializer(vats, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "value": 23,
        }
        response = self.client.post(
            "/api/v1/vat/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "value": 0.12345678910,
        }
        response = self.client.post(
            "/api/v1/vat/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/vat/{self.vat.id}/")
        vat = Vat.objects.get(id=self.vat.id)
        serializer = VatSerializer(vat)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_id = Vat.objects.latest("id").id + 1
        response = self.client.get(f"/api/v1/vat/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "value": 27,
        }
        response = self.client.put(
            f"/api/v1/vat/{self.vat.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "value": 0.12345678910,
        }
        response = self.client.put(
            f"/api/v1/vat/{self.vat.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "value": 30,
        }
        response = self.client.patch(
            f"/api/v1/vat/{self.vat.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "value": 0.12345678910,
        }
        response = self.client.patch(
            f"/api/v1/vat/{self.vat.id}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/vat/{self.vat.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_id = Vat.objects.latest("id").id + 1
        response = self.client.delete(f"/api/v1/vat/{invalid_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
