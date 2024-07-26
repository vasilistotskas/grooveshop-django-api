from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.favourite import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.serializers.favourite import ProductFavouriteSerializer
from user.factories.account import UserAccountFactory

User = get_user_model()


class ProductFavouriteViewSetTestCase(APITestCase):
    user: User = None
    product: Product = None
    product_favourite: ProductFavourite = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_favourite = ProductFavouriteFactory(
            product=self.product,
            user=self.user,
        )

    @staticmethod
    def get_product_favourite_detail_url(pk):
        return reverse("product-favourite-detail", args=[pk])

    @staticmethod
    def get_product_favourite_list_url():
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
        serializer = ProductFavouriteSerializer(favourite)

        self.assertEqual(response.data, serializer.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_product_favourite_id = 999999
        url = self.get_product_favourite_detail_url(invalid_product_favourite_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "product": self.product.id,
            "user": self.user.id,
        }

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.put(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "invalid_product",
            "user": "invalid_user",
        }

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.put(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "product": self.product.id,
        }

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.patch(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "product": "invalid_product",
        }

        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.patch(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_favourite_detail_url(self.product_favourite.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProductFavourite.objects.filter(pk=self.product_favourite.id).exists())

    def test_destroy_invalid(self):
        invalid_product_favourite_id = 999999
        url = self.get_product_favourite_detail_url(invalid_product_favourite_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        Product.objects.all().delete()
        ProductFavourite.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
