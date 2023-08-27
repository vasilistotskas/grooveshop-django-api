from django.test import RequestFactory
from django.test import TestCase
from rest_framework.request import Request

from core.pagination.cursor import CursorPaginator
from product.models.product import Product


class CursorPaginatorTest(TestCase):
    factory: RequestFactory = None

    def setUp(self):
        self.factory = RequestFactory()
        # Create some test data
        for i in range(1, 101):
            Product.objects.create(
                slug=f"product-{i}",
                price=10.00,
                active=True,
                stock=10,
                discount_percent=5.00,
                hits=0,
                weight=0.00,
            )

    def test_paginate_queryset(self):
        paginator = CursorPaginator()
        queryset = Product.objects.all()

        # Create a Request instance from Django REST framework
        request = Request(self.factory.get("/api/v1/products/?c=MA==&page_size=10"))

        paginated_queryset = paginator.paginate_queryset(queryset, request)

        self.assertEqual(len(paginated_queryset), 10)

    def test_get_total_pages(self):
        paginator = CursorPaginator()
        paginator.total_items = 105
        total_pages = paginator.get_total_pages()

        self.assertEqual(total_pages, 3)  # 105 items should be split into 3 pages

    def test_get_paginated_response(self):
        paginator = CursorPaginator()
        data = [{"name": f"Item {i}"} for i in range(1, 11)]
        response = paginator.get_paginated_response(data)

        self.assertIn("links", response.data)
        self.assertIn("count", response.data)
        self.assertIn("total_pages", response.data)
        self.assertIn("page_size", response.data)
        self.assertIn("page_total_results", response.data)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["page_total_results"], len(data))

    def tearDown(self) -> None:
        super().tearDown()
        Product.objects.all().delete()
