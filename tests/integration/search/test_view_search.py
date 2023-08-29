from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.product import Product

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SearchProductAPITest(APITestCase):
    product1: Product = None
    product2: Product = None
    product3: Product = None
    product4: Product = None

    def setUp(self):
        # Create some test products
        self.product1 = Product.objects.create(
            product_code="P123456",
            slug="Samsung-Galaxy-Z-Fold5-5G-Dual-SIM-12GB-512GB-Phantom-Black",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            hits=10,
            weight=Decimal("5.00"),
        )
        self.product2 = Product.objects.create(
            product_code="P123457",
            slug="Michelin-Pilot-Sport-5-225-45-R17-94Y-XL-THerino-Lasticho-gia-Epivatiko-Aytokinito-371721",
            price=Decimal("200.00"),
            active=True,
            stock=20,
            discount_percent=Decimal("25.0"),
            hits=20,
            weight=Decimal("10.00"),
        )
        self.product3 = Product.objects.create(
            product_code="P123458",
            slug="Nike-Park-VII-Andriko-Athlitiko-T-shirt-Kontomaniko-Dri-Fit-Mayro-BV6708-010",
            price=Decimal("300.00"),
            active=True,
            stock=30,
            discount_percent=Decimal("10.0"),
            hits=30,
            weight=Decimal("15.00"),
        )
        self.product4 = Product.objects.create(
            product_code="P123459",
            slug="Nike-Victori-One-Slides-se-Mayro-CHroma-CN9675-002",
            price=Decimal("400.00"),
            active=True,
            stock=40,
            discount_percent=Decimal("0.0"),
            hits=40,
            weight=Decimal("20.00"),
        )

        for language in languages:
            self.product1.set_current_language(language)
            self.product1.name = (
                "Samsung Galaxy Z Fold5 5G Dual SIM (12GB/512GB) Phantom Black"
            )
            self.product1.description = (
                "With an innovative form factor enhanced by "
                "the new Flex Hinge for a balanced design and professional camera "
                "capabilities with the unique FlexCam, the Galaxy Z series offers "
                "unrivaled foldable device experiences."
            )
            self.product1.save()

            self.product2.set_current_language(language)
            self.product2.name = (
                "Michelin Pilot Sport 5 225/45 R17 94Y XL Summer Passenger Car Tire"
            )
            self.product2.description = (
                "Pilot Sport 5 Michelin summer car tyre."
                " This type of tire is suitable for use in high temperatures."
                " This particular one is suitable for passenger vehicles with"
                " tire dimensions 225/45R17 and load index 94 and speed index Y."
            )
            self.product2.save()

            self.product3.set_current_language(language)
            self.product3.name = (
                "Nike Park VII Men's Sports T-shirt Short Sleeve Dri-Fit Black"
            )
            self.product3.description = (
                "Add comfort and freedom of movement to"
                " your workout with this t-shirt from Nike.Users who have bought it"
                " stand out mainly because the product is comfortable and looks "
                "like the photo shows."
            )
            self.product3.save()

            self.product4.set_current_language(language)
            self.product4.name = "Nike Victori One Slides in Black Color"
            self.product4.description = (
                "The Nike Victori One is designed for comfort"
                " and support, with a soft strap and a foam midsole."
                " The contoured footbed cradles your foot in comfort,"
                " while the durable rubber outsole provides "
                "traction on a variety of surfaces."
            )
            self.product4.save()

        self.product1.set_current_language(default_language)
        self.product2.set_current_language(default_language)
        self.product3.set_current_language(default_language)

    def test_search_product(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": "nike"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_search_product_with_language(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": "nike", "language": default_language})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_search_product_by_name(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": "nike park"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_no_results_found(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": "nonexistent"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data, "No results found")

    def test_pagination(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": "nike", "page": 1, "page_size": 1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["count"], 2)  # Total number of results
        self.assertIsNotNone(response.data["links"]["next"])

    def test_invalid_query(self):
        url = reverse("search-product")

        response = self.client.get(url, {"query": ""})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self) -> None:
        super().tearDown()
        self.product1.delete()
        self.product2.delete()
        self.product3.delete()
