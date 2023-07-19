from __future__ import annotations

import json

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from pay_way.models import PayWay
from pay_way.serializers import PayWaySerializer


class PayWayViewSetTestCase(APITestCase):
    pay_way: PayWay

    def setUp(self):
        self.pay_way = PayWay.objects.create(
            active=True, cost=10, free_for_order_amount=100
        )

    def test_list(self):
        response = self.client.get("/api/v1/pay_way/")
        pay_ways = PayWay.objects.all()
        serializer = PayWaySerializer(pay_ways, many=True)
        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "translations": {},
            "active": True,
            "cost": 5,
            "free_for_order_amount": 50,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]

            translation_payload = {
                "name": "Credit Card",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.post(
            "/api/v1/pay_way/", json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "name": "INVALID",
            "active": "INVALID",
            "cost": "INVALID",
            "free_for_order_amount": "INVALID",
        }
        response = self.client.post(
            "/api/v1/pay_way/", json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        response = self.client.get(f"/api/v1/pay_way/{self.pay_way.pk}/")
        pay_way = PayWay.objects.get(pk=self.pay_way.pk)
        serializer = PayWaySerializer(pay_way)
        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_pay_way = "invalid"
        response = self.client.get(f"/api/v1/pay_way/{invalid_pay_way}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "translations": {},
            "active": True,
            "cost": 5,
            "free_for_order_amount": 50,
        }

        for language in settings.LANGUAGES:
            language_code = language[0]

            translation_payload = {
                "name": "Credit Card",
            }

            payload["translations"][language_code] = translation_payload

        response = self.client.put(
            f"/api/v1/pay_way/{self.pay_way.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "name": "INVALID",
            "active": "INVALID",
            "cost": "INVALID",
            "free_for_order_amount": "INVALID",
        }
        response = self.client.put(
            f"/api/v1/pay_way/{self.pay_way.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "active": False,
        }
        response = self.client.patch(
            f"/api/v1/pay_way/{self.pay_way.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "name": "INVALID",
            "active": "INVALID",
            "cost": "INVALID",
            "free_for_order_amount": "INVALID",
        }
        response = self.client.patch(
            f"/api/v1/pay_way/{self.pay_way.pk}/",
            json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        response = self.client.delete(f"/api/v1/pay_way/{self.pay_way.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_destroy_invalid(self):
        invalid_pay_way = "invalid"
        response = self.client.delete(f"/api/v1/pay_way/{invalid_pay_way}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
