from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from product.serializers.favourite import (
    ProductFavouriteDetailSerializer,
    ProductFavouriteSerializer,
)
from user.factories.account import UserAccountFactory

User = get_user_model()


class ProductFavouriteViewSetTestCase(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_favourite = ProductFavouriteFactory(
            product=self.product,
            user=self.user,
        )

    def get_product_favourite_detail_url(self, pk):
        return reverse("product-favourite-detail", args=[pk])

    def get_product_favourite_list_url(self):
        return reverse("product-favourite-list")

    def test_list(self):
        url = self.get_product_favourite_list_url()
        response = self.client.get(url)
        favourites = ProductFavourite.objects.all()
        serializer = ProductFavouriteSerializer(favourites, many=True)

        self.assertEqual(response.data["results"], serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)

        payload = {
            "product": product_2.id,
            "user": self.user.id,
        }

        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "invalid_product",
            "user": "invalid_user",
        }

        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.get(url)
        favourite = ProductFavourite.objects.get(pk=self.product_favourite.id)
        serializer = ProductFavouriteDetailSerializer(favourite)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_product_favourite_id = 999999
        url = self.get_product_favourite_detail_url(
            invalid_product_favourite_id
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_uses_correct_serializer(self):
        url = self.get_product_favourite_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if response.data.get("results"):
            favourite_data = response.data["results"][0]
            expected_fields = {
                "id",
                "user_id",
                "user_username",
                "product",
                "product_name",
                "product_price",
                "created_at",
                "uuid",
            }
            self.assertTrue(
                expected_fields.issubset(set(favourite_data.keys()))
            )

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = {
            "id",
            "user",
            "product",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        payload = {"product": product_2.id}

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        expected_fields = {"product"}
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        self.assertEqual(response.data["product"], product_2.id)

    def test_update_request_response_serializers(self):
        payload = {"product": self.product.id}

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.put(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = {"product"}
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_partial_update_request_response_serializers(self):
        payload = {"product": self.product.id}

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.patch(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = {"product"}
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_with_complex_payload(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        payload = {
            "product": product_2.id,
        }

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["product"], product_2.id)

    def test_validation_errors_consistent(self):
        payload = {"product": 99999}

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", response.data)

    def test_duplicate_favourite_validation(self):
        payload = {"product": self.product.id}

        self.client.force_authenticate(user=self.user)
        url = self.get_product_favourite_list_url()
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product is already in favorites", str(response.data))

    def test_product_action(self):
        url = reverse(
            "product-favourite-product", args=[self.product_favourite.id]
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.product.id)
        expected_fields = {"id", "slug", "category", "price", "active", "stock"}
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_favourites_by_products_action(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        ProductFavouriteFactory(user=self.user, product=product_2)

        payload = {"product_ids": [self.product.id, product_2.id]}

        self.client.force_authenticate(user=self.user)
        url = reverse("product-favourite-favourites-by-products")
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_favourites_by_products_validation(self):
        payload = {}

        self.client.force_authenticate(user=self.user)
        url = reverse("product-favourite-favourites-by-products")
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filtering_functionality(self):
        user_2 = UserAccountFactory(num_addresses=0)
        ProductFavouriteFactory(user=user_2, product=self.product)

        url = self.get_product_favourite_list_url()

        response = self.client.get(url, {"user_id": self.user.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user_ids = [fav["user_id"] for fav in response.data["results"]]
        self.assertTrue(all(uid == self.user.id for uid in user_ids))

        response = self.client.get(url, {"product_id": self.product.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_ids = [fav["product"] for fav in response.data["results"]]
        self.assertTrue(all(pid == self.product.id for pid in product_ids))

    def test_ordering_functionality(self):
        product_2 = ProductFactory(num_images=0, num_reviews=0)
        ProductFavouriteFactory(user=self.user, product=product_2)

        url = self.get_product_favourite_list_url()

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if len(response.data["results"]) >= 2:
            first_created = response.data["results"][0]["created_at"]
            second_created = response.data["results"][1]["created_at"]
            self.assertGreaterEqual(first_created, second_created)

    def test_search_functionality(self):
        url = self.get_product_favourite_list_url()

        response = self.client.get(url, {"search": str(self.user.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_consistency_with_manual_serializer_instantiation(self):
        favourite = ProductFavourite.objects.get(pk=self.product_favourite.id)

        manual_serializer = ProductFavouriteDetailSerializer(favourite)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], manual_serializer.data["id"])
        self.assertEqual(response.data["uuid"], manual_serializer.data["uuid"])

    def test_queryset_optimization(self):
        url = self.get_product_favourite_list_url()

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if response.data.get("results"):
            favourite_data = response.data["results"][0]
            self.assertIsNotNone(favourite_data.get("user_id"))
            self.assertIsNotNone(favourite_data.get("product"))

    def test_update_valid(self):
        payload = {
            "product": self.product.id,
            "user": self.user.id,
        }

        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.put(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "invalid_product",
            "user": "invalid_user",
        }

        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.put(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "product": self.product.id,
        }

        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.patch(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "product": "invalid_product",
        }

        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.patch(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        self.client.force_authenticate(user=self.user)

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ProductFavourite.objects.filter(
                pk=self.product_favourite.id
            ).exists()
        )

    def test_destroy_invalid(self):
        invalid_product_favourite_id = 999999
        url = self.get_product_favourite_detail_url(
            invalid_product_favourite_id
        )
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
