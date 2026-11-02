"""``GET builder/link-targets``: the slug of each thing a link points at,
for the storefront to build the path; an absent id is one that a publish
would also have refused."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db


def get(**query):
    return APIClient().get(reverse("builder:link-targets"), query)


class TestResolving:
    def test_each_kind_answers_the_slug_of_its_ids(self, tenant):
        product = ProductFactory(active=True)
        category = ProductCategoryFactory(active=True)
        post = BlogPostFactory(is_published=True)
        blog_category = BlogCategoryFactory()

        response = get(
            product=product.pk,
            category=category.pk,
            blogPost=post.pk,
            blogCategory=blog_category.pk,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "product": {str(product.pk): product.slug},
            "category": {str(category.pk): category.slug},
            "blogPost": {str(post.pk): post.slug},
            "blogCategory": {str(blog_category.pk): blog_category.slug},
        }

    def test_only_the_kinds_asked_about_are_answered(self, tenant):
        product = ProductFactory(active=True)

        assert get(product=product.pk).json() == {
            "product": {str(product.pk): product.slug}
        }
        assert get().json() == {}

    def test_several_ids_of_a_kind_are_comma_separated(self, tenant):
        first, second = ProductFactory.create_batch(2, active=True)

        body = get(product=f"{first.pk},{second.pk}").json()

        assert body == {
            "product": {str(first.pk): first.slug, str(second.pk): second.slug}
        }

    def test_what_a_publish_would_refuse_is_absent(self, tenant):
        inactive = ProductFactory(active=False)
        draft = BlogPostFactory(is_published=False)
        hidden = ProductCategoryFactory(active=False)
        shown = ProductFactory(active=True)

        body = get(
            product=f"{shown.pk},{inactive.pk},987654",
            blogPost=draft.pk,
            category=hidden.pk,
        ).json()

        assert body == {
            "product": {str(shown.pk): shown.slug},
            "blogPost": {},
            "category": {},
        }

    def test_it_costs_one_query_per_kind(
        self, tenant, django_assert_num_queries
    ):
        products = ProductFactory.create_batch(3, active=True)
        ids = ",".join(str(p.pk) for p in products)

        with django_assert_num_queries(2):
            response = get(product=ids, blogPost="1,2,3")

        assert response.status_code == status.HTTP_200_OK


class TestRefusing:
    @pytest.mark.parametrize("value", ["abc", "1,x", "0", "-3", "1.5"])
    def test_an_id_that_is_not_a_positive_integer_is_a_400(self, tenant, value):
        response = get(product=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_link_targets"
        assert response.json()["param"] == "product"

    def test_more_ids_than_a_call_may_resolve_is_a_400(self, tenant, settings):
        settings.BUILDER_LINK_TARGETS_MAX_IDS = 3

        assert get(blogPost="1,2,3").status_code == status.HTTP_200_OK
        response = get(blogPost="1,2,3,4")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["param"] == "blogPost"
