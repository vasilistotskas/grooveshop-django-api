from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.product import Product


class SearchProductAPITest(APITestCase):
    product1 = None
    product2 = None
    product3 = None

    def setUp(self):
        # Create some test products
        self.product1 = Product.objects.create(
            slug="product_one",
            description="Description A",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )
        self.product2 = Product.objects.create(
            slug="product_two",
            description="Description B",
            price=20.00,
            active=True,
            stock=20,
            discount_percent=10.00,
            hits=0,
            weight=0.00,
        )
        self.product3 = Product.objects.create(
            slug="product_three",
            description="Description C",
            price=30.00,
            active=True,
            stock=30,
            discount_percent=15.00,
            hits=0,
            weight=0.00,
        )

    def test_search_products(self):
        url = reverse("search-product")
        response = self.client.get(url, {"query": "1"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_search_products_no_results(self):
        url = reverse("search-product")
        response = self.client.get(url, {"query": "Nonexistent"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 0)
