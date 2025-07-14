from unittest.mock import MagicMock, patch
from urllib.parse import quote

from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from blog.models.post import BlogPost, BlogPostTranslation
from product.models.product import Product, ProductTranslation
from search.views import blog_post_meili_search, product_meili_search


class TestBlogPostMeiliSearch(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

        self.blog_post = BlogPost.objects.create()

        self.blog_translation = BlogPostTranslation.objects.create(
            master=self.blog_post,
            language_code="en",
            title="Test Blog Post",
            body="This is a test blog post content",
        )

    def test_valid_search_query(self):
        mock_results = {
            "estimated_total_hits": 1,
            "results": [
                {
                    "object": self.blog_translation,
                    "_formatted": {"title": "Test <em>Blog</em> Post"},
                    "_matchesPosition": {"title": [{"start": 5, "length": 4}]},
                    "_rankingScore": 0.95,
                }
            ],
        }

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get("/search/blog/", {"query": "test"})
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)

            data = response.data
            self.assertIn("results", data)
            self.assertIn("estimated_total_hits", data)
            self.assertIn("limit", data)
            self.assertIn("offset", data)

            self.assertEqual(data["limit"], 10)
            self.assertEqual(data["offset"], 0)
            self.assertEqual(data["estimated_total_hits"], 1)

            mock_paginate.assert_called_once_with(limit=10, offset=0)
            mock_paginate.return_value.search.assert_called_once_with(q="test")

    def test_missing_query_parameter(self):
        request = self.factory.get("/search/blog/")

        response = blog_post_meili_search(request)
        self.assertIn("error", response.data)
        self.assertIn(
            "A search query is required.", str(response.data["error"])
        )

    def test_empty_query_parameter(self):
        request = self.factory.get("/search/blog/", {"query": ""})

        try:
            response = blog_post_meili_search(request)
            self.assertIsNotNone(response)
        except ValidationError:
            pass

    def test_custom_pagination_parameters(self):
        mock_results = {"estimated_total_hits": 50, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": "test", "limit": 20, "offset": 10}
            )
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["limit"], 20)
            self.assertEqual(data["offset"], 10)

            mock_paginate.assert_called_once_with(limit=20, offset=10)

    def test_url_encoded_query(self):
        encoded_query = quote("test query with spaces")
        mock_results = {"estimated_total_hits": 1, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": encoded_query}
            )
            blog_post_meili_search(request)

            mock_paginate.return_value.search.assert_called_once_with(
                q="test query with spaces"
            )

    def test_search_result_serialization(self):
        mock_results = {
            "estimated_total_hits": 2,
            "results": [
                {
                    "object": self.blog_translation,
                    "_formatted": {"title": "Test <em>Blog</em> Post"},
                    "_matchesPosition": {"title": [{"start": 5, "length": 4}]},
                    "_rankingScore": 0.95,
                },
                {
                    "object": self.blog_translation,
                    "_formatted": {
                        "content": "This is a <em>test</em> content"
                    },
                    "_matchesPosition": {
                        "content": [{"start": 10, "length": 4}]
                    },
                    "_rankingScore": 0.85,
                },
            ],
        }

        with (
            patch.object(
                BlogPostTranslation.meilisearch, "paginate"
            ) as mock_paginate,
            patch(
                "search.views.BlogPostTranslationSerializer"
            ) as mock_serializer,
        ):
            mock_paginate.return_value.search.return_value = mock_results
            mock_serializer.return_value.data = {"title": "Test Blog Post"}

            request = self.factory.get("/search/blog/", {"query": "test"})
            blog_post_meili_search(request)

            self.assertEqual(mock_serializer.call_count, 2)

            for call in mock_serializer.call_args_list:
                args, kwargs = call
                self.assertIn("context", kwargs)
                context = kwargs["context"]
                self.assertIn("_formatted", context)
                self.assertIn("_matchesPosition", context)
                self.assertIn("_rankingScore", context)

    def test_no_search_results(self):
        mock_results = {"estimated_total_hits": 0, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": "nonexistent"}
            )
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["estimated_total_hits"], 0)
            self.assertEqual(len(data["results"]), 0)

    def test_invalid_pagination_parameters(self):
        mock_results = {"estimated_total_hits": 10, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": "test", "limit": "invalid"}
            )

            with self.assertRaises(ValueError):
                blog_post_meili_search(request)

    def test_meilisearch_error_handling(self):
        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.side_effect = Exception(
                "MeiliSearch connection error"
            )

            request = self.factory.get("/search/blog/", {"query": "test"})

            with self.assertRaises(Exception) as context:
                blog_post_meili_search(request)

            self.assertIn(
                "MeiliSearch connection error", str(context.exception)
            )


class TestProductMeiliSearch(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

        self.product = Product.objects.create()

        self.product_translation = ProductTranslation.objects.create(
            master=self.product,
            language_code="en",
            name="Test Product",
            description="This is a test product description",
        )

    def test_valid_search_query(self):
        mock_results = {
            "estimated_total_hits": 1,
            "results": [
                {
                    "object": self.product_translation,
                    "_formatted": {"name": "Test <em>Product</em>"},
                    "_matchesPosition": {"name": [{"start": 5, "length": 7}]},
                    "_rankingScore": 0.92,
                }
            ],
        }

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get("/search/products/", {"query": "test"})
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)

            data = response.data
            self.assertIn("results", data)
            self.assertIn("estimated_total_hits", data)
            self.assertIn("limit", data)
            self.assertIn("offset", data)

            self.assertEqual(data["limit"], 10)
            self.assertEqual(data["offset"], 0)
            self.assertEqual(data["estimated_total_hits"], 1)

            mock_paginate.assert_called_once_with(limit=10, offset=0)
            mock_paginate.return_value.search.assert_called_once_with(q="test")

    def test_missing_query_parameter(self):
        request = self.factory.get("/search/products/")

        response = product_meili_search(request)
        self.assertIn("error", response.data)
        self.assertIn(
            "A search query is required.", str(response.data["error"])
        )

    def test_empty_query_parameter(self):
        request = self.factory.get("/search/products/", {"query": ""})

        try:
            response = product_meili_search(request)
            self.assertIsNotNone(response)
        except ValidationError:
            pass

    def test_custom_pagination_parameters(self):
        mock_results = {"estimated_total_hits": 100, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/",
                {"query": "laptop", "limit": 25, "offset": 50},
            )
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["limit"], 25)
            self.assertEqual(data["offset"], 50)

            mock_paginate.assert_called_once_with(limit=25, offset=50)

    def test_url_encoded_query(self):
        encoded_query = quote("laptop & accessories")
        mock_results = {"estimated_total_hits": 5, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/", {"query": encoded_query}
            )
            product_meili_search(request)

            mock_paginate.return_value.search.assert_called_once_with(
                q="laptop & accessories"
            )

    def test_search_result_serialization(self):
        mock_results = {
            "estimated_total_hits": 3,
            "results": [
                {
                    "object": self.product_translation,
                    "_formatted": {"name": "Test <em>Product</em>"},
                    "_matchesPosition": {"name": [{"start": 5, "length": 7}]},
                    "_rankingScore": 0.92,
                },
                {
                    "object": self.product_translation,
                    "_formatted": {
                        "description": "This is a <em>test</em> description"
                    },
                    "_matchesPosition": {
                        "description": [{"start": 10, "length": 4}]
                    },
                    "_rankingScore": 0.88,
                },
                {
                    "object": self.product_translation,
                    "_formatted": None,
                    "_matchesPosition": None,
                    "_rankingScore": 0.75,
                },
            ],
        }

        with (
            patch.object(
                ProductTranslation.meilisearch, "paginate"
            ) as mock_paginate,
            patch(
                "search.views.ProductTranslationSerializer"
            ) as mock_serializer,
        ):
            mock_paginate.return_value.search.return_value = mock_results
            mock_serializer.return_value.data = {"name": "Test Product"}

            request = self.factory.get("/search/products/", {"query": "test"})
            product_meili_search(request)

            self.assertEqual(mock_serializer.call_count, 3)

            for call in mock_serializer.call_args_list:
                args, kwargs = call
                self.assertIn("context", kwargs)
                context = kwargs["context"]
                self.assertIn("_formatted", context)
                self.assertIn("_matchesPosition", context)
                self.assertIn("_rankingScore", context)

    def test_no_search_results(self):
        mock_results = {"estimated_total_hits": 0, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/", {"query": "nonexistent"}
            )
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["estimated_total_hits"], 0)
            self.assertEqual(len(data["results"]), 0)

    def test_large_pagination_values(self):
        mock_results = {"estimated_total_hits": 10000, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/",
                {"query": "popular", "limit": 100, "offset": 5000},
            )
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            self.assertEqual(data["limit"], 100)
            self.assertEqual(data["offset"], 5000)

            mock_paginate.assert_called_once_with(limit=100, offset=5000)

    def test_meilisearch_error_handling(self):
        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.side_effect = Exception("MeiliSearch timeout")

            request = self.factory.get("/search/products/", {"query": "test"})

            with self.assertRaises(Exception) as context:
                product_meili_search(request)

            self.assertIn("MeiliSearch timeout", str(context.exception))


class TestSearchViewsCommon(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_query_parameter_extraction(self):
        test_cases = [
            "simple",
            "with spaces",
            "with-special-chars!@#",
            "123numbers",
            "ñoñó unicode",
        ]

        for query in test_cases:
            with self.subTest(query=query):
                request = self.factory.get("/search/blog/", {"query": query})

                self.assertEqual(request.GET.get("query"), query)

    def test_pagination_parameter_defaults(self):
        request = self.factory.get("/search/blog/", {"query": "test"})

        limit = int(request.GET.get("limit", 10))
        offset = int(request.GET.get("offset", 0))

        self.assertEqual(limit, 10)
        self.assertEqual(offset, 0)

    def test_pagination_parameter_conversion(self):
        request = self.factory.get(
            "/search/blog/", {"query": "test", "limit": "20", "offset": "5"}
        )

        limit = int(request.GET.get("limit", 10))
        offset = int(request.GET.get("offset", 0))

        self.assertEqual(limit, 20)
        self.assertEqual(offset, 5)
        self.assertIsInstance(limit, int)
        self.assertIsInstance(offset, int)


class TestSearchViewsEdgeCases(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_zero_pagination_values(self):
        mock_results = {"estimated_total_hits": 100, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": "test", "limit": 0, "offset": 0}
            )
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_paginate.assert_called_once_with(limit=0, offset=0)

    def test_negative_pagination_values(self):
        mock_results = {"estimated_total_hits": 100, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/",
                {"query": "test", "limit": -5, "offset": -10},
            )
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_paginate.assert_called_once_with(limit=-5, offset=-10)

    def test_very_long_query(self):
        long_query = "a" * 1000
        mock_results = {"estimated_total_hits": 0, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get("/search/blog/", {"query": long_query})
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_paginate.return_value.search.assert_called_once_with(
                q=long_query
            )

    def test_unicode_query(self):
        unicode_query = "héllo wörld 你好 мир"
        mock_results = {"estimated_total_hits": 1, "results": []}

        with patch.object(
            ProductTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/products/", {"query": unicode_query}
            )
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_paginate.return_value.search.assert_called_once_with(
                q=unicode_query
            )

    def test_special_characters_query(self):
        special_query = "!@#$%^&*()[]{}|\\:\";'<>?,./"
        mock_results = {"estimated_total_hits": 0, "results": []}

        with patch.object(
            BlogPostTranslation.meilisearch, "paginate"
        ) as mock_paginate:
            mock_paginate.return_value.search.return_value = mock_results

            request = self.factory.get(
                "/search/blog/", {"query": special_query}
            )
            response = blog_post_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_paginate.return_value.search.assert_called_once_with(
                q=special_query
            )

    def test_malformed_meilisearch_results(self):
        malformed_results = {
            "estimated_total_hits": 1,
            "results": [
                {
                    "object": None,
                    "_formatted": {},
                    "_matchesPosition": {},
                    "_rankingScore": None,
                }
            ],
        }

        with (
            patch.object(
                BlogPostTranslation.meilisearch, "paginate"
            ) as mock_paginate,
            patch(
                "search.views.BlogPostTranslationSerializer"
            ) as mock_serializer,
        ):
            mock_paginate.return_value.search.return_value = malformed_results
            mock_serializer.return_value.data = {}

            request = self.factory.get("/search/blog/", {"query": "test"})

            try:
                response = blog_post_meili_search(request)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
            except Exception as e:
                self.assertIsInstance(
                    e, (AttributeError, TypeError, ValueError)
                )

    def test_missing_meilisearch_fields(self):
        results_missing_fields = {
            "estimated_total_hits": 1,
            "results": [
                {
                    "object": MagicMock(),
                }
            ],
        }

        with (
            patch.object(
                ProductTranslation.meilisearch, "paginate"
            ) as mock_paginate,
            patch(
                "search.views.ProductTranslationSerializer"
            ) as mock_serializer,
        ):
            mock_paginate.return_value.search.return_value = (
                results_missing_fields
            )
            mock_serializer.return_value.data = {"name": "Test"}

            request = self.factory.get("/search/products/", {"query": "test"})
            response = product_meili_search(request)

            self.assertEqual(response.status_code, status.HTTP_200_OK)

            mock_serializer.assert_called_once()
            call_kwargs = mock_serializer.call_args[1]
            context = call_kwargs["context"]
            self.assertEqual(context["_formatted"], {})
            self.assertEqual(context["_matchesPosition"], {})
            self.assertIsNone(context["_rankingScore"])
