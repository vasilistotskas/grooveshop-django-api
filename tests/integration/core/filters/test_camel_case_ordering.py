from django.urls import reverse
from rest_framework.request import Request
from rest_framework.test import APITestCase

from blog.factories import BlogAuthorFactory, BlogPostFactory
from blog.models.post import BlogPost
from blog.views.post import BlogPostViewSet
from core.filters.camel_case_ordering import CamelCaseOrderingFilter
from user.factories import UserAccountFactory


class CamelCaseOrderingFilterTest(APITestCase):
    def setUp(self):
        self.view = BlogPostViewSet.as_view({"get": "list"})
        self.filter = CamelCaseOrderingFilter()
        self.author1 = BlogAuthorFactory(user=UserAccountFactory())
        self.author2 = BlogAuthorFactory(user=UserAccountFactory())

        BlogPostFactory(author=self.author1)
        BlogPostFactory(author=self.author2)

    def _get_ordering(self, response, queryset):
        drf_request = Request(response.wsgi_request)
        view = BlogPostViewSet()
        view.request = drf_request
        view.format_kwarg = None
        view.kwargs = {}
        view.action = "list"
        return self.filter.get_ordering(drf_request, queryset, view)

    def _get_default_ordering(self):
        view = BlogPostViewSet()
        return self.filter.get_default_ordering(view)

    def create_request(self, ordering_value):
        url = reverse("blog-post-list")
        return self.client.get(url, {"ordering": ordering_value})

    def test_get_ordering_camel_case(self):
        request = self.create_request("createdAt")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["created_at"])

    def test_get_ordering_pascal_case(self):
        request = self.create_request("CreatedAt")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["created_at"])

    def test_get_ordering_mixed_case(self):
        request = self.create_request("CreatedAt,updatedAt")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["created_at", "updated_at"])

    def test_get_ordering_snake_case(self):
        request = self.create_request("created_at,updated_at")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["created_at", "updated_at"])

    def test_get_ordering_with_direction(self):
        request = self.create_request("-createdAt,updatedAt")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["-created_at", "updated_at"])

    def test_get_ordering_mixed_directions(self):
        request = self.create_request("-CreatedAt,updatedAt,-viewCount")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["-created_at", "updated_at", "-view_count"])

    def test_get_ordering_empty(self):
        request = self.create_request("")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        default_ordering = self._get_default_ordering()
        self.assertEqual(result, default_ordering)
        self.assertEqual(result, ["-created_at"])

    def test_get_ordering_no_param(self):
        url = reverse("blog-post-list")
        request = self.client.get(url)
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        default_ordering = self._get_default_ordering()
        self.assertEqual(result, default_ordering)
        self.assertEqual(result, ["-created_at"])

    def test_get_ordering_invalid_field(self):
        request = self.create_request("createdAt,invalidField,-updatedAt")
        queryset = BlogPost.objects.all()
        result = self._get_ordering(request, queryset)
        self.assertEqual(result, ["created_at", "-updated_at"])

    def test_camel_to_snake_conversion(self):
        test_cases = [
            ("createdAt", "created_at"),
            ("CreatedAt", "created_at"),
            ("HTTPResponse", "http_response"),
            ("getHTTPResponseCode", "get_http_response_code"),
            ("already_snake_case", "already_snake_case"),
            ("alreadySnakeCase", "already_snake_case"),
            ("IOError", "io_error"),
            ("XMLParser", "xml_parser"),
            ("findXMLParser", "find_xml_parser"),
            ("id", "id"),
            ("ID", "id"),
            ("sortOrder", "sort_order"),
            ("isPublished", "is_published"),
            ("HTTPSProxy", "https_proxy"),
            ("HTTPAPI", "httpapi"),
            ("isHTTPSEnabled", "is_https_enabled"),
            ("viewCount", "view_count"),
            ("likesCount", "likes_count"),
            ("commentsCount", "comments_count"),
            ("tagsCount", "tags_count"),
            ("publishedAt", "published_at"),
        ]

        for input_str, expected in test_cases:
            with self.subTest(input=input_str):
                result = CamelCaseOrderingFilter.camel_to_snake(input_str)
                self.assertEqual(result, expected)

    def test_actual_api_ordering(self):
        url = reverse("blog-post-list")

        response = self.client.get(url, {"ordering": "createdAt"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {"ordering": "CreatedAt"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {"ordering": "-createdAt"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {"ordering": "-publishedAt,viewCount"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.status_code, 200)
