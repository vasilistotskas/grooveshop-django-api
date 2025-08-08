from decimal import Decimal
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from product.factories.category import ProductCategoryFactory
from product.models.product import Product
from vat.factories import VatFactory


class ProductFilterTest(APITestCase):
    def setUp(self):
        Product.objects.all().delete()

        self.category1 = ProductCategoryFactory(name="Electronics")
        self.category2 = ProductCategoryFactory(name="Clothing")
        self.vat = VatFactory(value=Decimal("20.0"))

        self.now = timezone.now()

        self.expensive_product = Product.objects.create(
            sku="EXPENSIVE-001",
            category=self.category1,
            price=Decimal("1000.00"),
            active=True,
            stock=5,
            discount_percent=Decimal("10.0"),
            vat=self.vat,
            view_count=500,
            weight=Decimal("2.5"),
        )
        Product.objects.filter(id=self.expensive_product.id).update(
            created_at=self.now - timedelta(days=30),
            updated_at=self.now - timedelta(days=25),
        )
        self.expensive_product.refresh_from_db()

        self.cheap_product = Product.objects.create(
            sku="CHEAP-001",
            category=self.category2,
            price=Decimal("50.00"),
            active=True,
            stock=100,
            discount_percent=Decimal("0.0"),
            vat=self.vat,
            view_count=1000,
            weight=Decimal("0.5"),
        )
        Product.objects.filter(id=self.cheap_product.id).update(
            created_at=self.now - timedelta(hours=2),
            updated_at=self.now - timedelta(hours=1),
        )
        self.cheap_product.refresh_from_db()

        self.out_of_stock_product = Product.objects.create(
            sku="OUTSTOCK-001",
            category=self.category1,
            price=Decimal("200.00"),
            active=True,
            stock=0,
            discount_percent=Decimal("25.0"),
            vat=self.vat,
            view_count=250,
            weight=Decimal("1.0"),
        )
        Product.objects.filter(id=self.out_of_stock_product.id).update(
            created_at=self.now - timedelta(days=7),
            updated_at=self.now - timedelta(days=5),
        )
        self.out_of_stock_product.refresh_from_db()

        self.inactive_product = Product.objects.create(
            sku="INACTIVE-001",
            category=self.category2,
            price=Decimal("75.00"),
            active=False,
            stock=20,
            discount_percent=Decimal("5.0"),
            vat=self.vat,
            view_count=50,
            weight=Decimal("0.8"),
        )
        Product.objects.filter(id=self.inactive_product.id).update(
            created_at=self.now - timedelta(days=14),
            updated_at=self.now - timedelta(days=10),
        )
        self.inactive_product.refresh_from_db()

    def test_basic_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"id": self.expensive_product.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.expensive_product.id
        )

        response = self.client.get(url, {"sku": "EXPENSIVE-001"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.expensive_product.id
        )

        response = self.client.get(url, {"sku__icontains": "CHEAP"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.cheap_product.id
        )

    def test_active_filter(self):
        url = reverse("product-list")

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertNotIn(self.inactive_product.id, result_ids)

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.inactive_product.id, result_ids)

    def test_price_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"min_price": "100"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(url, {"max_price": "100"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.cheap_product.id, result_ids)
        self.assertIn(self.inactive_product.id, result_ids)

        response = self.client.get(url, {"min_price": "50", "max_price": "200"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)

    def test_stock_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"min_stock": "10"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.cheap_product.id, result_ids)
        self.assertIn(self.inactive_product.id, result_ids)

        response = self.client.get(url, {"max_stock": "20"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)

        response = self.client.get(url, {"in_stock": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertNotIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(url, {"in_stock": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.out_of_stock_product.id, result_ids)

    def test_discount_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"min_discount_percent": "10"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(url, {"has_discount": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertNotIn(self.cheap_product.id, result_ids)

        response = self.client.get(url, {"has_discount": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.cheap_product.id, result_ids)

    def test_category_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"category_id": self.category1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(url, {"category": str(self.category2.id)})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.cheap_product.id, result_ids)
        self.assertIn(self.inactive_product.id, result_ids)

    def test_view_count_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"min_view_count": "300"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.cheap_product.id, result_ids)

        response = self.client.get(url, {"max_view_count": "500"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertNotIn(self.cheap_product.id, result_ids)

    def test_weight_filters(self):
        url = reverse("product-list")

        response = self.client.get(url, {"min_weight": "1.0"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(url, {"max_weight": "1.0"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertNotIn(self.expensive_product.id, result_ids)

    def test_timestamp_filters(self):
        url = reverse("product-list")

        created_after_date = self.now - timedelta(days=14)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.expensive_product.id, result_ids)
        self.assertIn(self.cheap_product.id, result_ids)

        created_before_date = self.now - timedelta(days=14)
        response = self.client.get(
            url, {"created_before": created_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.expensive_product.id, result_ids)
        self.assertIn(self.inactive_product.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("product-list")

        response = self.client.get(
            url, {"uuid": str(self.expensive_product.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.expensive_product.id
        )

    def test_camel_case_filters(self):
        url = reverse("product-list")

        created_after_date = self.now - timedelta(days=14)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "active": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.expensive_product.id, result_ids)
        self.assertNotIn(self.inactive_product.id, result_ids)
        self.assertIn(self.cheap_product.id, result_ids)
        self.assertIn(self.out_of_stock_product.id, result_ids)

        response = self.client.get(
            url,
            {
                "minPrice": "100",
                "maxPrice": "500",
                "inStock": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

        response = self.client.get(
            url,
            {
                "minPrice": "100",
                "maxPrice": "1500",
                "inStock": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.expensive_product.id, result_ids)

    def test_existing_filters_still_work(self):
        url = reverse("product-list")

        response = self.client.get(
            url,
            {
                "active": "true",
                "min_price": "50",
                "max_stock": "50",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.expensive_product.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("product-list")

        created_after_date = self.now - timedelta(days=20)

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "active": "true",
                "minPrice": "50",
                "inStock": "true",
                "hasDiscount": "false",
                "ordering": "-view_count",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.cheap_product.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("product-list")

        response = self.client.get(
            url, {"active": "true", "ordering": "-price"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 3)

        product_ids = [r["id"] for r in results]
        self.assertEqual(product_ids[0], self.expensive_product.id)

    def test_metadata_filters_if_available(self):
        url = reverse("product-list")

        has_metadata = hasattr(Product, "metadata")

        if has_metadata:
            response = self.client.get(url, {"metadata_has_key": "test_key"})
            self.assertEqual(response.status_code, 200)
        else:
            pass

    def test_soft_delete_filters_if_available(self):
        url = reverse("product-list")

        has_soft_delete = hasattr(Product, "is_deleted")

        if has_soft_delete:
            response = self.client.get(url, {"is_deleted": "false"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data["results"]), 4)
        else:
            pass

    def test_multiple_category_filter(self):
        url = reverse("product-list")

        category_param = f"{self.category1.id}_{self.category2.id}"
        response = self.client.get(url, {"category": category_param})
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)

    def tearDown(self):
        Product.objects.all().delete()
