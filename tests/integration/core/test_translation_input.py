"""`translations` is caller-supplied and reached the database unchecked.

`TranslatedFieldExtended.to_internal_value` (and parler-rest's original
underneath it) parsed the multipart JSON string without a guard and never
looked at the KEYS. Three outcomes, all verified against
`POST /api/v1/blog/comment`, which any signed-in customer can reach:

* `translations=not-json{` -> `json.JSONDecodeError` -> HTTP 500
* `{"xx": {...}}`          -> HTTP 201, storing a translation row every
  read path filters out
* a 40-character code      -> `DataError: value too long for type
  character varying(15)` -> HTTP 500
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.models.comment import BlogComment
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer_client():
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory(num_addresses=0))
    return client


@pytest.fixture
def visible_post():
    return BlogPostFactory(is_published=True)


def test_unparseable_multipart_translations_is_a_400(
    customer_client, visible_post
):
    response = customer_client.post(
        reverse("blog-comment-list"),
        data={"post": str(visible_post.pk), "translations": "not-json{"},
        format="multipart",
    )

    assert response.status_code == 400, response.status_code
    assert "translations" in response.data


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("unknown", "xx"),
        ("longer than the column", "x" * 40),
        ("empty", ""),
        ("a locale rather than a language", "en-GB"),
    ],
)
def test_an_unsupported_language_code_is_a_400(
    customer_client, visible_post, label, code
):
    response = customer_client.post(
        reverse("blog-comment-list"),
        {"post": visible_post.pk, "translations": {code: {"content": "hi"}}},
        format="json",
    )

    assert response.status_code == 400, f"{label}: {response.status_code}"
    assert not BlogComment.objects.exists()


def test_a_supported_language_still_writes(customer_client, visible_post):
    response = customer_client.post(
        reverse("blog-comment-list"),
        {"post": visible_post.pk, "translations": {"el": {"content": "γεια"}}},
        format="json",
    )

    assert response.status_code == 201, response.data
    comment = BlogComment.objects.get(pk=response.data["id"])
    assert list(
        comment.translations.values_list("language_code", flat=True)
    ) == ["el"]


def test_the_supported_set_comes_from_parler_not_a_literal():
    from django.conf import settings

    from core.utils.i18n import available_language_codes

    assert available_language_codes() == frozenset(
        entry["code"] for entry in settings.PARLER_LANGUAGES[settings.SITE_ID]
    )
