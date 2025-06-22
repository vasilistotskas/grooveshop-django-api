from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from pay_way.enum.pay_way import PayWayEnum
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class PayWayViewSetTestCase(APITestCase):
    def setUp(self):
        self.pay_way = PayWayFactory(
            active=True,
            cost=10.00,
            free_threshold=100.00,
        )

    def get_pay_way_detail_url(self, pk):
        return reverse("payway-detail", args=[pk])

    def get_pay_way_list_url(self):
        return reverse("payway-list")

    def test_list(self):
        url = self.get_pay_way_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        found = False
        for item in response.data["results"]:
            if item["active"] == self.pay_way.active:
                cost = item["cost"]
                if isinstance(cost, dict):
                    self.assertAlmostEqual(
                        float(cost["amount"]),
                        float(self.pay_way.cost.amount),
                        places=2,
                    )
                    self.assertEqual(
                        cost["currency"], str(self.pay_way.cost.currency)
                    )
                else:
                    self.assertAlmostEqual(
                        float(cost), float(self.pay_way.cost.amount), places=2
                    )
                found = True
                break

        self.assertTrue(found, "Could not find the created pay_way in response")

    def test_create_valid(self):
        payload = {
            "active": True,
            "cost": 10.00,
            "free_threshold": 100.00,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]

            translation_payload = {
                "name": PayWayEnum.CREDIT_CARD,
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_pay_way_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "active": "invalid_active",
            "cost": "invalid_cost",
            "free_threshold": "invalid_amount",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_pay_way_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["active"], self.pay_way.active)

        cost = response.data["cost"]
        if isinstance(cost, dict):
            self.assertAlmostEqual(
                float(cost["amount"]), float(self.pay_way.cost.amount), places=2
            )
            self.assertEqual(cost["currency"], str(self.pay_way.cost.currency))
        else:
            self.assertAlmostEqual(
                float(cost), float(self.pay_way.cost.amount), places=2
            )

        free_amount = response.data["free_threshold"]
        if isinstance(free_amount, dict):
            self.assertAlmostEqual(
                float(free_amount["amount"]),
                float(self.pay_way.free_threshold.amount),
                places=2,
            )
            self.assertEqual(
                free_amount["currency"],
                str(self.pay_way.free_threshold.currency),
            )
        else:
            self.assertAlmostEqual(
                float(free_amount),
                float(self.pay_way.free_threshold.amount),
                places=2,
            )

    def test_retrieve_invalid(self):
        invalid_pay_way_id = 9999
        url = self.get_pay_way_detail_url(invalid_pay_way_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "active": False,
            "cost": 20.00,
            "free_threshold": 200.00,
            "translations": {},
        }

        for language in languages:
            language_code = language[0]

            translation_payload = {
                "name": PayWayEnum.PAY_ON_STORE,
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "active": "invalid_active",
            "cost": "invalid_cost",
            "free_threshold": "invalid_amount",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "active": False,
            "translations": {
                default_language: {
                    "name": PayWayEnum.PAY_ON_STORE,
                }
            },
        }

        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "cost": "invalid_cost",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                }
            },
        }

        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_pay_way_detail_url(self.pay_way.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PayWay.objects.filter(pk=self.pay_way.pk).exists())

    def test_destroy_invalid(self):
        invalid_pay_way_id = 9999
        url = self.get_pay_way_detail_url(invalid_pay_way_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
