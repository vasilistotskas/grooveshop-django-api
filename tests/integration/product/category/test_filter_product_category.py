from decimal import Decimal
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from product.factories.product import ProductFactory
from product.models.category import ProductCategory
from product.models.product import Product


class ProductCategoryFilterTest(APITestCase):
    def setUp(self):
        ProductCategory.objects.all().delete()
        Product.objects.all().delete()

        self.now = timezone.now()

        self.root_electronics = ProductCategory.objects.create(
            slug="electronics",
            active=True,
            sort_order=100,
        )
        ProductCategory.objects.filter(id=self.root_electronics.id).update(
            created_at=self.now - timedelta(days=30),
            updated_at=self.now - timedelta(days=25),
        )
        self.root_electronics.refresh_from_db()

        self.root_clothing = ProductCategory.objects.create(
            slug="clothing",
            active=True,
            sort_order=200,
        )
        ProductCategory.objects.filter(id=self.root_clothing.id).update(
            created_at=self.now - timedelta(days=20),
            updated_at=self.now - timedelta(days=15),
        )
        self.root_clothing.refresh_from_db()

        self.root_books = ProductCategory.objects.create(
            slug="books",
            active=False,
            sort_order=300,
        )
        ProductCategory.objects.filter(id=self.root_books.id).update(
            created_at=self.now - timedelta(days=10),
            updated_at=self.now - timedelta(days=5),
        )
        self.root_books.refresh_from_db()

        self.child_phones = ProductCategory.objects.create(
            slug="phones",
            active=True,
            parent=self.root_electronics,
            sort_order=110,
        )
        ProductCategory.objects.filter(id=self.child_phones.id).update(
            created_at=self.now - timedelta(days=15),
            updated_at=self.now - timedelta(days=10),
        )
        self.child_phones.refresh_from_db()

        self.child_computers = ProductCategory.objects.create(
            slug="computers",
            active=True,
            parent=self.root_electronics,
            sort_order=120,
        )
        ProductCategory.objects.filter(id=self.child_computers.id).update(
            created_at=self.now - timedelta(days=12),
            updated_at=self.now - timedelta(days=8),
        )
        self.child_computers.refresh_from_db()

        self.grandchild_laptops = ProductCategory.objects.create(
            slug="laptops",
            active=True,
            parent=self.child_computers,
            sort_order=121,
        )
        ProductCategory.objects.filter(id=self.grandchild_laptops.id).update(
            created_at=self.now - timedelta(days=8),
            updated_at=self.now - timedelta(days=3),
        )
        self.grandchild_laptops.refresh_from_db()

        self.phone_product = ProductFactory(
            category=self.child_phones, price=Decimal("500.00"), active=True
        )

        self.laptop_product1 = ProductFactory(
            category=self.grandchild_laptops,
            price=Decimal("1000.00"),
            active=True,
        )

        self.laptop_product2 = ProductFactory(
            category=self.grandchild_laptops,
            price=Decimal("1500.00"),
            active=True,
        )

        self.clothing_product = ProductFactory(
            category=self.root_clothing, price=Decimal("50.00"), active=True
        )

        ProductCategory.objects.rebuild()

    def test_basic_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"id": self.root_electronics.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.root_electronics.id
        )

        response = self.client.get(url, {"slug__icontains": "electron"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.root_electronics.id
        )

        response = self.client.get(url, {"slug": "phones"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.child_phones.id
        )

    def test_active_filter(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        active_categories = ProductCategory.objects.filter(active=True)
        self.assertEqual(len(result_ids), active_categories.count())
        self.assertNotIn(self.root_books.id, result_ids)

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        inactive_categories = ProductCategory.objects.filter(active=False)
        self.assertEqual(len(result_ids), inactive_categories.count())
        self.assertIn(self.root_books.id, result_ids)

    def test_hierarchy_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"parent": self.root_electronics.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_children = [self.child_phones.id, self.child_computers.id]
        self.assertEqual(len(result_ids), len(expected_children))
        for child_id in expected_children:
            self.assertIn(child_id, result_ids)

        response = self.client.get(url, {"parent_slug": "computers"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.grandchild_laptops.id, result_ids)

        response = self.client.get(url, {"level": 0})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        root_categories = ProductCategory.objects.filter(level=0)
        self.assertEqual(len(result_ids), root_categories.count())

        response = self.client.get(url, {"min_level": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        non_root_categories = ProductCategory.objects.filter(level__gte=1)
        self.assertEqual(len(result_ids), non_root_categories.count())

        response = self.client.get(url, {"max_level": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_max_level_1 = ProductCategory.objects.filter(level__lte=1)
        self.assertEqual(len(result_ids), categories_max_level_1.count())
        self.assertNotIn(self.grandchild_laptops.id, result_ids)

    def test_root_and_leaf_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"is_root": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        root_categories = ProductCategory.objects.filter(parent__isnull=True)
        self.assertEqual(len(result_ids), root_categories.count())

        response = self.client.get(url, {"is_root": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        non_root_categories = ProductCategory.objects.filter(
            parent__isnull=False
        )
        self.assertEqual(len(result_ids), non_root_categories.count())

        response = self.client.get(url, {"is_leaf": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        leaf_categories = ProductCategory.objects.filter(children__isnull=True)
        self.assertEqual(len(result_ids), leaf_categories.count())

        response = self.client.get(url, {"has_children": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_with_children = ProductCategory.objects.filter(
            children__isnull=False
        ).distinct()
        self.assertEqual(len(result_ids), categories_with_children.count())

    def test_product_count_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"has_products": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        for cat_id in result_ids:
            category = ProductCategory.objects.get(id=cat_id)
            descendants = category.get_descendants(include_self=True)
            has_products = Product.objects.filter(
                category__in=descendants
            ).exists()
            self.assertTrue(has_products)

        response = self.client.get(url, {"min_product_count": "2"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        for cat_id in result_ids:
            category = ProductCategory.objects.get(id=cat_id)
            descendants = category.get_descendants(include_self=True)
            product_count = Product.objects.filter(
                category__in=descendants
            ).count()
            self.assertGreaterEqual(product_count, 2)

    def test_ancestor_descendant_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(
            url, {"descendant_of": self.root_electronics.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        descendants = self.root_electronics.get_descendants()
        expected_ids = list(descendants.values_list("id", flat=True))
        self.assertEqual(len(result_ids), len(expected_ids))
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(
            url, {"ancestor_of": self.grandchild_laptops.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        ancestors = self.grandchild_laptops.get_ancestors()
        expected_ids = list(ancestors.values_list("id", flat=True))
        self.assertEqual(len(result_ids), len(expected_ids))
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"sibling_of": self.child_phones.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        siblings = self.child_phones.get_siblings()
        expected_ids = list(siblings.values_list("id", flat=True))
        self.assertEqual(len(result_ids), len(expected_ids))
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_sort_order_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"sort_order": 200})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_with_200 = ProductCategory.objects.filter(sort_order=200)
        self.assertEqual(len(result_ids), categories_with_200.count())

        if len(result_ids) > 0:
            self.assertIn(self.root_clothing.id, result_ids)

        response = self.client.get(url, {"sort_order_min": 200})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_min_200 = ProductCategory.objects.filter(sort_order__gte=200)
        self.assertEqual(len(result_ids), categories_min_200.count())

        response = self.client.get(url, {"sort_order_max": 120})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_max_120 = ProductCategory.objects.filter(sort_order__lte=120)
        self.assertEqual(len(result_ids), categories_max_120.count())

    def test_timestamp_filters(self):
        url = reverse("product-category-list")

        created_after_date = self.now - timedelta(days=15)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_after_date = ProductCategory.objects.filter(
            created_at__gte=created_after_date
        )
        self.assertEqual(len(result_ids), categories_after_date.count())

        for result_id in result_ids:
            category = ProductCategory.objects.get(id=result_id)
            self.assertGreaterEqual(category.created_at, created_after_date)

        updated_before_date = self.now - timedelta(days=10)
        response = self.client.get(
            url, {"updated_before": updated_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        categories_before_date = ProductCategory.objects.filter(
            updated_at__lte=updated_before_date
        )
        self.assertEqual(len(result_ids), categories_before_date.count())

        for result_id in result_ids:
            category = ProductCategory.objects.get(id=result_id)
            self.assertLessEqual(category.updated_at, updated_before_date)

    def test_uuid_filter(self):
        url = reverse("product-category-list")

        response = self.client.get(
            url, {"uuid": str(self.root_electronics.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.root_electronics.id
        )

    def test_camel_case_filters(self):
        url = reverse("product-category-list")

        created_after_date = self.now - timedelta(days=15)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "active": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_categories = ProductCategory.objects.filter(
            created_at__gte=created_after_date, active=True
        )
        self.assertEqual(len(result_ids), expected_categories.count())

        response = self.client.get(
            url,
            {
                "minLevel": 1,
                "maxLevel": 1,
                "isLeaf": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        level_1_leaf_categories = ProductCategory.objects.filter(
            level=1, children__isnull=True
        )
        self.assertEqual(len(result_ids), level_1_leaf_categories.count())

        response = self.client.get(
            url,
            {
                "sortOrderMin": 110,
                "sortOrderMax": 200,
                "hasChildren": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        matching_categories = ProductCategory.objects.filter(
            sort_order__gte=110, sort_order__lte=200, children__isnull=True
        )
        self.assertEqual(len(result_ids), matching_categories.count())

    def test_existing_filters_still_work(self):
        url = reverse("product-category-list")

        response = self.client.get(
            url,
            {
                "active": "true",
                "min_level": 1,
                "has_children": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        matching_categories = ProductCategory.objects.filter(
            active=True, level__gte=1, children__isnull=True
        )
        self.assertEqual(len(result_ids), matching_categories.count())

    def test_complex_filter_combinations(self):
        url = reverse("product-category-list")

        created_after_date = self.now - timedelta(days=20)

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "active": "true",
                "hasProducts": "true",
                "minLevel": 1,
                "ordering": "sort_order",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        for result_id in result_ids:
            category = ProductCategory.objects.get(id=result_id)

            self.assertGreaterEqual(category.created_at, created_after_date)

            self.assertTrue(category.active)

            descendants = category.get_descendants(include_self=True)
            has_products = Product.objects.filter(
                category__in=descendants
            ).exists()
            self.assertTrue(has_products)

            self.assertGreaterEqual(category.level, 1)

        results = response.data["results"]
        if len(results) > 1:
            sort_orders = [r.get("sort_order", 0) for r in results]
            self.assertEqual(sort_orders, sorted(sort_orders))

    def test_filter_with_ordering(self):
        url = reverse("product-category-list")

        response = self.client.get(
            url, {"active": "true", "ordering": "-sort_order"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        active_categories = ProductCategory.objects.filter(active=True)
        self.assertEqual(len(results), active_categories.count())

        if len(results) > 1:
            sort_orders = [r.get("sort_order", 0) for r in results]
            self.assertEqual(sort_orders, sorted(sort_orders, reverse=True))

    def test_edge_cases(self):
        url = reverse("product-category-list")

        response = self.client.get(url, {"descendant_of": 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

        response = self.client.get(url, {"ancestor_of": 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

        response = self.client.get(url, {"sibling_of": 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

        response = self.client.get(url, {"min_product_count": 100})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_hierarchy_specific_filters(self):
        url = reverse("product-category-list")

        response = self.client.get(
            url,
            {
                "parent": self.root_electronics.id,
                "level": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        direct_children = ProductCategory.objects.filter(
            parent=self.root_electronics, level=1
        )
        self.assertEqual(len(result_ids), direct_children.count())

        response = self.client.get(
            url,
            {
                "isLeaf": "true",
                "active": "true",
                "ordering": "level",
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        active_leaf_categories = ProductCategory.objects.filter(
            children__isnull=True, active=True
        )
        self.assertEqual(len(results), active_leaf_categories.count())

        if len(results) > 1:
            levels = [r.get("level", 0) for r in results]
            self.assertEqual(levels, sorted(levels))

    def tearDown(self):
        ProductCategory.objects.all().delete()
        Product.objects.all().delete()
