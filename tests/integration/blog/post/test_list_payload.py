"""A list of posts carries what a card shows, never the article.

The post cards read the title and subtitle, the counts and the image; the
body (a whole article), the SEO copy and the list of users who liked the
post belong to the post page. They used to ride in every list, and in
every list a post is embedded in, which put 100 to 200 KB of other posts'
articles into the page payload of a post, a category and an author.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.author import BlogAuthorFactory
from blog.factories.post import BlogPostFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

CARD_TRANSLATION_FIELDS = {"title", "subtitle"}
ARTICLE_TRANSLATION_FIELDS = {
    "body",
    "seoTitle",
    "seoDescription",
    "seoKeywords",
}


@pytest.fixture
def post():
    author = BlogAuthorFactory(user=UserAccountFactory())
    post = BlogPostFactory(author=author, featured=True)
    post.likes.add(UserAccountFactory(), UserAccountFactory())
    return post


@pytest.fixture
def client():
    return APIClient()


def _assert_card(item):
    assert "likes" not in item
    assert item["likesCount"] == 2
    assert item["contentPreview"]
    assert item["translations"], "no translations at all"
    for fields in item["translations"].values():
        assert set(fields) == CARD_TRANSLATION_FIELDS
        assert fields["title"]
        assert not ARTICLE_TRANSLATION_FIELDS & set(fields)


@pytest.mark.parametrize("route", ["list", "trending", "popular", "featured"])
def test_post_lists_are_cards(client, post, route):
    name = "blog-post-list" if route == "list" else f"blog-post-{route}"

    response = client.get(reverse(name))

    assert response.status_code == status.HTTP_200_OK
    results = response.json()["results"]
    _assert_card(next(item for item in results if item["id"] == post.id))


def test_related_posts_are_cards(client, post):
    other = BlogPostFactory(category=post.category, author=post.author)
    other.likes.add(UserAccountFactory(), UserAccountFactory())

    response = client.get(reverse("blog-post-related_posts", args=[post.id]))

    assert response.status_code == status.HTTP_200_OK
    for item in response.json():
        assert "likes" not in item
        for fields in item["translations"].values():
            assert set(fields) == CARD_TRANSLATION_FIELDS


def test_category_and_author_post_lists_are_cards(client, post):
    for url in (
        reverse("blog-category-posts", args=[post.category_id]),
        reverse("blog-author-posts", args=[post.author_id]),
    ):
        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        _assert_card(
            next(
                item
                for item in response.json()["results"]
                if item["id"] == post.id
            )
        )


def test_the_posts_an_author_embeds_are_cards(client, post):
    response = client.get(reverse("blog-author-detail", args=[post.author_id]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["recentPosts"] and body["topPosts"]
    for item in (*body["recentPosts"], *body["topPosts"]):
        assert "likes" not in item
        for fields in item["translations"].values():
            assert set(fields) == CARD_TRANSLATION_FIELDS


def test_the_post_page_keeps_the_article(client, post):
    response = client.get(reverse("blog-post-detail", args=[post.id]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["likes"]) == 2
    for fields in body["translations"].values():
        assert ARTICLE_TRANSLATION_FIELDS <= set(fields)
        assert fields["body"]
    # The author it embeds lists its other posts as cards.
    for item in body["author"]["recentPosts"]:
        assert "likes" not in item
        assert "body" not in next(iter(item["translations"].values()))
