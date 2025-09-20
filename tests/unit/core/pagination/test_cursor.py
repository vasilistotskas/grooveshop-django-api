from django.test import RequestFactory, TestCase
from rest_framework.request import Request

from core.pagination.cursor import CursorPaginator
from product.factories.product import ProductFactory
from product.models.product import Product


class CursorPaginatorTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        ProductFactory.create_batch(4, num_images=0, num_reviews=0)

    def test_paginate_queryset(self):
        paginator = CursorPaginator()
        queryset = Product.objects.all()

        request = Request(self.factory.get("/api/v1/products/?page_size=2"))

        paginated_queryset = paginator.paginate_queryset(queryset, request)

        self.assertIsNotNone(paginated_queryset)
        self.assertLessEqual(len(paginated_queryset), 2)

    def test_get_total_pages(self):
        paginator = CursorPaginator()
        paginator.total_items = 105
        paginator.page_size = 50
        total_pages = paginator.get_total_pages()

        self.assertEqual(total_pages, 3)

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
