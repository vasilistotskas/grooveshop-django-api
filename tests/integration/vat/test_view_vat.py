from __future__ import annotations

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from vat.models import Vat
from vat.serializers import VatSerializer


class VatViewSetTestCase(APITestCase):
    vat: Vat = None

    def setUp(self):
        self.vat = Vat.objects.create(
            value=21.0,
        )

    @staticmethod
    def get_vat_detail_url(pk):
        return reverse("vat-detail", kwargs={"pk": pk})

    @staticmethod
    def get_vat_list_url():
        return reverse("vat-list")

    def test_list(self):
        url = self.get_vat_list_url()
        response = self.client.get(url)
        vats = Vat.objects.all()
        serializer = VatSerializer(vats, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "value": 25.0,
        }
        url = self.get_vat_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "value": "invalid_value",
        }

        url = self.get_vat_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_vat_detail_url(self.vat.id)
        response = self.client.get(url)
        vat = Vat.objects.get(id=self.vat.id)
        serializer = VatSerializer(vat)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_vat_id = 9999
        url = self.get_vat_detail_url(invalid_vat_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "value": 25.0,
        }

        url = self.get_vat_detail_url(self.vat.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "value": "invalid_value",
        }

        url = self.get_vat_detail_url(self.vat.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "value": 25.0,
        }

        url = self.get_vat_detail_url(self.vat.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "value": "invalid_value",
        }

        url = self.get_vat_detail_url(self.vat.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_vat_detail_url(self.vat.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Vat.objects.filter(pk=self.vat.pk).exists())

    def test_destroy_invalid(self):
        invalid_vat_id = 9999
        url = self.get_vat_detail_url(invalid_vat_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.vat.delete()
