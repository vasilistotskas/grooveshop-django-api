"""The category list answers the paginated envelope its schema declares.

``GET /blog/category?tree=true`` used to answer a bare array of the root
categories, while the schema documents the list as
``PaginatedBlogCategoryList`` and does not know the parameter at all, so a
client generated from the document could not parse that body. The tree has
its own documented route, ``GET /blog/category/tree``; the list is the list
whatever else is in its query string.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.category import BlogCategoryFactory
from tests.utils.openapi import assert_matches_response

pytestmark = pytest.mark.django_db

LIST = "/api/v1/blog/category"


@pytest.mark.parametrize("query", [{}, {"tree": "true"}])
def test_the_list_matches_the_schema(openapi_schema, query):
    root = BlogCategoryFactory(slug="root")
    BlogCategoryFactory(slug="child", parent=root)

    response = APIClient().get(reverse("blog-category-list"), query)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert_matches_response(openapi_schema, LIST, body)
    assert {item["slug"] for item in body["results"]} == {"root", "child"}
