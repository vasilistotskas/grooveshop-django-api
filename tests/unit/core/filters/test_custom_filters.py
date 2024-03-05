from django.urls import reverse
from rest_framework.test import APITestCase

from blog.models.post import BlogPost
from blog.views.post import BlogPostViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter


class PascalSnakeCaseOrderingFilterTest(APITestCase):
    view: BlogPostViewSet = None
    filter: PascalSnakeCaseOrderingFilter = None

    def setUp(self):
        self.view = BlogPostViewSet.as_view({"get": "list"})
        self.filter = PascalSnakeCaseOrderingFilter()

    def create_request(self, ordering_value):
        """
        Helper method to create a request object with the desired query parameter.
        """
        url = reverse("blog-post-list")
        return self.client.get(url, {"ordering": ordering_value})

    def test_get_ordering_pascal_case(self):
        request = self.create_request("CreatedAt")
        queryset = BlogPost.objects.all()
        result = self.filter.get_ordering(
            request.renderer_context["request"], queryset, self.view
        )
        self.assertEqual(result, ["created_at"])

    def test_get_ordering_mixed_case(self):
        request = self.create_request("CreatedAt,UpdatedAt")
        queryset = BlogPost.objects.all()
        result = self.filter.get_ordering(
            request.renderer_context["request"], queryset, self.view
        )
        self.assertEqual(result, ["created_at", "updated_at"])

    def test_get_ordering_snake_case(self):
        request = self.create_request("created_at,updated_at")
        queryset = BlogPost.objects.all()
        result = self.filter.get_ordering(
            request.renderer_context["request"], queryset, self.view
        )
        self.assertEqual(result, ["created_at", "updated_at"])

    def test_get_ordering_empty(self):
        request = self.create_request("")
        queryset = BlogPost.objects.all()
        result = self.filter.get_ordering(
            request.renderer_context["request"], queryset, self.view
        )
        default_ordering = self.filter.get_default_ordering(self.view)
        self.assertEqual(result, default_ordering)

    def tearDown(self) -> None:
        super().tearDown()
        BlogPost.objects.all().delete()
