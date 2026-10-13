"""A store feature that is switched off is off on every route serving it.

Each feature's own routes answer 404 while it is off. These are the
routes that serve the same data from somewhere else — a product's
reviews, a post's comments, the account's own lists — and kept answering
after the switch-off. Off, they are gone the same way; on, they serve.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from product.factories.product import ProductFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def shopper():
    return UserAccountFactory(num_addresses=0)


def _url(route: str, owner: str, shopper) -> str:
    if owner == "product":
        pk = ProductFactory(active=True, num_images=0, num_reviews=0).pk
    elif owner == "post":
        pk = BlogPostFactory(num_tags=0, num_comments=0).pk
    else:
        pk = shopper.pk
    return reverse(route, kwargs={"pk": pk})


@pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
@pytest.mark.parametrize(
    ("switch", "route", "owner"),
    [
        ("PRODUCT_REVIEWS_ENABLED", "product-reviews", "product"),
        ("BLOG_COMMENTS_ENABLED", "blog-post-comments", "post"),
        (
            "FAVOURITES_ENABLED",
            "user-account-favourite-products",
            "account",
        ),
        ("PRODUCT_REVIEWS_ENABLED", "user-account-product-reviews", "account"),
        ("ACCOUNT_REVIEWS_ENABLED", "user-account-product-reviews", "account"),
        (
            "BLOG_COMMENTS_ENABLED",
            "user-account-blog-post-comments",
            "account",
        ),
    ],
)
def test_the_route_follows_the_store_switch(switch, route, owner, on, shopper):
    url = _url(route, owner, shopper)
    switch_store_settings(**{switch: on})
    client = APIClient()
    client.force_authenticate(user=shopper)

    response = client.get(url)

    expected = status.HTTP_200_OK if on else status.HTTP_404_NOT_FOUND
    assert response.status_code == expected, response.data


def test_liked_posts_are_gone_without_the_blog_plan(shopper):
    url = reverse("user-account-liked-blog-posts", kwargs={"pk": shopper.pk})
    client = APIClient()
    client.force_authenticate(user=shopper)

    previous = bind_store_tenant(store_tenant("no_blog", blog_enabled=False))
    try:
        response = client.get(url)
    finally:
        unbind_store_tenant(previous)

    assert response.status_code == status.HTTP_404_NOT_FOUND
