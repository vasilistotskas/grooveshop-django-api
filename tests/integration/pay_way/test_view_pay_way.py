from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.tests import compare_serializer_and_response
from helpers.seed import get_or_create_default_image
from pay_way.enum.pay_way_enum import PayWayEnum
from pay_way.models import PayWay
from pay_way.serializers import PayWaySerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
        },
    }
)
class PayWayViewSetTestCase(APITestCase):
    pay_way: PayWay = None

    def setUp(self):
        image_icon = get_or_create_default_image("uploads/pay_way/no_photo.jpg")
        self.pay_way = PayWay.objects.create(
            active=True,
            cost=10.00,
            free_for_order_amount=100.00,
            icon=image_icon,
        )
        for language in languages:
            self.pay_way.set_current_language(language)
            self.pay_way.name = PayWayEnum.CREDIT_CARD
            self.pay_way.save()
        self.pay_way.set_current_language(default_language)

    @staticmethod
    def get_pay_way_detail_url(pk):
        return reverse("payway-detail", args=[pk])

    @staticmethod
    def get_pay_way_list_url():
        return reverse("payway-list")

    def test_list(self):
        url = self.get_pay_way_list_url()
        response = self.client.get(url)
        pay_ways = PayWay.objects.all()
        serializer = PayWaySerializer(pay_ways, many=True)
        for response_item, serializer_item in zip(
            response.data["results"], serializer.data
        ):
            compare_serializer_and_response(serializer_item, response_item, ["icon"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "active": True,
            "cost": 10.00,
            "free_for_order_amount": 100.00,
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
            "free_for_order_amount": "invalid_amount",
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
        pay_way = PayWay.objects.get(pk=self.pay_way.pk)
        serializer = PayWaySerializer(pay_way)
        compare_serializer_and_response(serializer.data, response.data, ["icon"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_pay_way_id = 9999  # An ID that doesn't exist in the database
        url = self.get_pay_way_detail_url(invalid_pay_way_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "active": False,
            "cost": 20.00,
            "free_for_order_amount": 200.00,
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
            "free_for_order_amount": "invalid_amount",
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
        invalid_pay_way_id = 9999  # An ID that doesn't exist in the database
        url = self.get_pay_way_detail_url(invalid_pay_way_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.pay_way.delete()
