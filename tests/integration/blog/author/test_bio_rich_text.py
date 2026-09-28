"""The author bio is rich text, like every other long merchant-written
text: the page rendered the old plain-text bio as one block, because
HTML collapses the blank lines between its paragraphs.
"""

from __future__ import annotations

import importlib

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from blog.admin import BlogAuthorAdmin
from blog.factories.author import BlogAuthorFactory
from blog.models.author import BlogAuthor
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "blog.migrations.0039_author_bio_rich_text"
)


def _historical_apps():
    # What the migration receives: the models as of the migration graph,
    # parler's translation bases included.
    return MigrationExecutor(connection).loader.project_state().apps


def _author():
    # Its own user: the factory's default reuses an existing one, and the
    # author is one-to-one with its user.
    return BlogAuthorFactory(user=UserAccountFactory())


def _translation(author):
    return author.translations.get(language_code="el")


def _store_raw(translation, bio):
    # A queryset update, as the old plain-text value sat in the column:
    # no field ``pre_save`` in between.
    type(translation).objects.filter(pk=translation.pk).update(bio=bio)


class TestBackfill:
    def test_blank_line_blocks_become_paragraphs(self):
        translation = _translation(_author())
        _store_raw(
            translation,
            "Tom & Jerry <3\r\n\r\nSecond paragraph,\r\nsame block.\r\n",
        )

        _migration._plain_bio_to_rich_text(_historical_apps(), None)

        translation.refresh_from_db()
        assert translation.bio == (
            "<p>Tom &amp; Jerry &lt;3</p>\n\n"
            "<p>Second paragraph,<br>same block.</p>"
        )

    def test_running_it_again_converts_nothing_twice(self):
        translation = _translation(_author())
        _store_raw(translation, "First.\r\n\r\nSecond.")

        _migration._plain_bio_to_rich_text(_historical_apps(), None)
        _migration._plain_bio_to_rich_text(_historical_apps(), None)

        translation.refresh_from_db()
        assert translation.bio == "<p>First.</p>\n\n<p>Second.</p>"

    def test_an_empty_or_missing_bio_is_left_alone(self):
        empty = _translation(_author())
        missing = _translation(_author())
        _store_raw(empty, "")
        _store_raw(missing, None)

        _migration._plain_bio_to_rich_text(_historical_apps(), None)

        empty.refresh_from_db()
        missing.refresh_from_db()
        assert empty.bio == ""
        assert missing.bio is None


class TestField:
    def test_markup_the_policy_would_strip_is_refused(self):
        author = _author()
        author.set_current_language("el")
        author.bio = "<p>Hello</p><script>alert(1)</script>"

        with pytest.raises(ValidationError):
            author.save()

    def test_formatting_is_kept(self):
        author = _author()
        author.set_current_language("el")
        author.bio = "<p><strong>Mike</strong> writes about ads.</p>"
        author.save()

        assert _translation(author).bio == (
            "<p><strong>Mike</strong> writes about ads.</p>"
        )


class TestAdminPreview:
    def test_previews_the_words_not_the_markup(self):
        author = _author()
        author.set_current_language("el")
        author.bio = (
            "<p><strong>Mike Ganos</strong> is a performance marketer "
            "who plans and runs media campaigns.</p>"
        )
        author.save()
        admin = BlogAuthorAdmin(BlogAuthor, AdminSite())

        preview = admin.bio_preview(BlogAuthor.objects.get(pk=author.pk))

        assert "<" not in preview
        assert preview.startswith("Mike Ganos is a performance")
        assert len(preview) <= 50

    def test_previews_entities_as_the_characters_they_are(self):
        author = _author()
        author.set_current_language("el")
        author.bio = "<p>Tom &amp; Jerry</p>"
        author.save()
        admin = BlogAuthorAdmin(BlogAuthor, AdminSite())

        preview = admin.bio_preview(BlogAuthor.objects.get(pk=author.pk))

        assert preview == "Tom & Jerry"
