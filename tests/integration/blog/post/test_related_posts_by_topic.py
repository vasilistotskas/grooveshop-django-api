"""Related posts lead with the posts of the same category on the same
topic, ranked by the distinctive words they share, and the rest of the
category fills the block."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import translation
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost, BlogPostTranslation
from blog.strategies.shared_terms_related_posts_strategy import (
    SharedTermsRelatedPostsStrategy,
    ranked_by_shared_terms,
)

pytestmark = pytest.mark.django_db

GREEK = "el"


def post_in(category, title, *, subtitle=""):
    return BlogPostFactory(
        category=category,
        translations=[
            BlogPostTranslation(
                language_code=GREEK, title=title, subtitle=subtitle, body=""
            )
        ],
    )


def related_ids(post) -> list[int]:
    response = APIClient().get(
        reverse("blog-post-related_posts", args=[post.pk]),
        HTTP_X_LANGUAGE=GREEK,
    )
    assert response.status_code == status.HTTP_200_OK, response.data
    rows = (
        response.data["results"]
        if isinstance(response.data, dict)
        else response.data
    )
    return [row["id"] for row in rows]


def ranked(post) -> list[int]:
    with translation.override(GREEK):
        return ranked_by_shared_terms(post)


def test_the_same_topic_leads_and_the_category_fills():
    phones = BlogCategoryFactory()
    post = post_in(
        phones, "Πώς καταλαβαίνεις αν κάποιος σε έχει σε σίγαση στο Instagram"
    )
    same_topic = post_in(phones, "Πώς να μη φαίνεσαι ενεργός στο Instagram")
    # Newer, same category, another topic: by date alone it came first.
    other_topic = post_in(phones, "Ποια κινητά υποστηρίζουν ασύρματη φόρτιση")
    elsewhere = post_in(
        BlogCategoryFactory(), "Πώς βλέπεις την τοποθεσία στο Instagram"
    )

    ids = related_ids(post)

    assert ids[0] == same_topic.pk
    assert other_topic.pk in ids
    assert elsewhere.pk not in ids
    assert post.pk not in ids


def test_words_many_titles_have_do_not_make_posts_alike():
    # A store's titles, like webside's, are mostly how-tos: «Πώς να … στο …»
    # is common across the blog, so it weighs little; «eSIM» is rare.
    elsewhere = BlogCategoryFactory()
    for thing in ("οθόνη", "μπαταρία", "μνήμη", "κάμερα", "WiFi", "ήχο"):
        post_in(elsewhere, f"Πώς να ρυθμίσεις την {thing} στο κινητό")
    category = BlogCategoryFactory()
    post = post_in(category, "Πώς να ενεργοποιήσεις το eSIM στο κινητό")
    common = post_in(category, "Πώς να καθαρίσεις τη μνήμη στο κινητό")
    distinctive = post_in(
        category, "Τι είναι η eSIM και ποιες εταιρείες τη δίνουν"
    )

    order = ranked(post)

    assert order.index(distinctive.pk) < order.index(common.pk)


def test_inflected_and_accented_forms_are_one_word():
    category = BlogCategoryFactory()
    post = post_in(category, "Η σίγαση συνομιλιών")
    inflected = post_in(category, "ΟΔΗΓΟΣ ΓΙΑ ΤΗ ΣΙΓΑΣΗ ΣΤΑ ΜΗΝΥΜΑΤΑ")
    unrelated = post_in(category, "Φόρτιση μπαταρίας")

    order = ranked(post)

    assert order[0] == inflected.pk
    assert unrelated.pk not in order


def test_a_post_without_a_category_has_no_topic_neighbours():
    post = post_in(None, "Οπτική ίνα")

    assert list(SharedTermsRelatedPostsStrategy().get_related_posts(post)) == []


def test_drafts_are_never_related():
    category = BlogCategoryFactory()
    post = post_in(category, "Οπτική ίνα και ταχύτητα")
    draft = post_in(category, "Οπτική ίνα: τα μειονεκτήματα")
    draft.is_published = False
    draft.save()

    assert draft.pk not in ranked(post)
    assert draft.pk not in related_ids(post)


def test_the_block_keeps_the_order_the_strategies_found():
    category = BlogCategoryFactory()
    post = post_in(category, "Οπτική ίνα")
    older, newer = (post_in(category, title) for title in ("Α", "Β"))
    first = BlogPost.objects.filter(pk__in=[older.pk])

    # The shared-words strategy ranks the older post first: it stays
    # first, ahead of a newer post the category strategy adds after it.
    with patch.object(
        SharedTermsRelatedPostsStrategy, "get_related_posts", return_value=first
    ):
        ids = related_ids(post)

    assert ids == [older.pk, newer.pk]


def test_the_text_search_configurations_exist():
    """A configuration named in the settings must exist in Postgres, or
    every related-posts request in that language fails."""
    from django.db import connection

    names = {
        *settings.TEXT_SEARCH_CONFIGS.values(),
        settings.TEXT_SEARCH_DEFAULT_CONFIG,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT cfgname FROM pg_ts_config WHERE cfgname = ANY(%s)",
            [list(names)],
        )
        assert {row[0] for row in cursor.fetchall()} == names
