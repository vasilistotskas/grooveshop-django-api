from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.db import connection
from PIL import Image

from blog.factories.post import BlogPostFactory
from core.management.commands.record_rich_text_image_sizes import (
    record_current_schema,
    rich_text_fields,
)
from core.utils.rich_text_images import record_natural_sizes
from core.utils.sanitize import sanitize_html

STATIC_HOST = "https://static.webside.gr"


@pytest.fixture
def media(settings, tmp_path: Path):
    """The saving store's media directory, and the URL prefix for it."""
    settings.MEDIA_URL = "https://static.example.test/media/"
    uploads = tmp_path / connection.schema_name / "uploads" / "tinymce"
    uploads.mkdir(parents=True)
    settings.MEDIA_ROOT = str(tmp_path)

    class Media:
        directory = uploads
        url = f"/media/{connection.schema_name}/uploads/tinymce/"

        @staticmethod
        def add(name: str, size: tuple[int, int], fmt: str = "PNG", **save):
            buffer = io.BytesIO()
            Image.new("RGB", size).save(buffer, fmt, **save)
            (uploads / name).write_bytes(buffer.getvalue())

    return Media


def _img(src: str, attrs: str = "") -> str:
    return sanitize_html(f'<p><img src="{src}" alt="a"{attrs}></p>')


def test_size_is_recorded_beside_the_authors_layout(media):
    media.add("a.png", (1200, 800))
    html = _img(f"{STATIC_HOST}{media.url}a.png", ' width="100%" height="100%"')

    assert record_natural_sizes(html) == (
        f'<p><img src="{STATIC_HOST}{media.url}a.png" alt="a" '
        'width="100%" height="100%" data-natural-width="1200" '
        'data-natural-height="800"></p>'
    )


@pytest.mark.parametrize(
    ("fmt", "name"), [("WEBP", "a.webp"), ("JPEG", "a.jpg")]
)
def test_formats_the_editor_uploads_are_measured(media, fmt, name):
    media.add(name, (640, 480), fmt)

    result = record_natural_sizes(_img(f"{media.url}{name}"))

    assert 'data-natural-width="640" data-natural-height="480"' in result


def test_avif_is_measured(media):
    media.add("a.avif", (320, 200), "AVIF")

    result = record_natural_sizes(_img(f"{media.url}a.avif"))

    assert 'data-natural-width="320" data-natural-height="200"' in result


def test_a_rotated_photo_is_measured_as_the_browser_shows_it(media):
    image = Image.new("RGB", (400, 300))
    exif = image.getexif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    (media.directory / "r.jpg").write_bytes(buffer.getvalue())

    result = record_natural_sizes(_img(f"{media.url}r.jpg"))

    assert 'data-natural-width="300" data-natural-height="400"' in result


def test_a_second_pass_changes_nothing(media):
    media.add("a.png", (10, 20))
    once = record_natural_sizes(_img(f"{media.url}a.png", ' width="75%"'))

    assert record_natural_sizes(once) == once
    assert sanitize_html(once) == once


def test_each_image_gets_its_own_size(media):
    media.add("a.png", (10, 20))
    media.add("b.png", (30, 40))
    html = sanitize_html(
        f'<p><img src="{media.url}a.png"></p><p>text</p>'
        f'<img src="{media.url}b.png"><img src="{media.url}a.png">'
    )

    result = record_natural_sizes(html)

    assert result.count('data-natural-width="10" data-natural-height="20"') == 2
    assert result.count('data-natural-width="30" data-natural-height="40"') == 1


@pytest.mark.parametrize(
    "src",
    [
        # Another host and another path: never fetched, nothing to read.
        "https://elsewhere.example/media/other/a.png",
        "https://elsewhere.example/a.png",
        # Not in the file's store.
        "/media/another_store/uploads/tinymce/a.png",
        # Path traversal out of the store's directory.
        "/media/{schema}/../../etc/passwd",
        "/media/{schema}/uploads/tinymce/missing.png",
        "/media/{schema}/",
        "",
    ],
)
def test_an_image_that_cannot_be_read_gets_no_size(media, src):
    html = sanitize_html(
        f'<img src="{src.format(schema=connection.schema_name)}">'
    )

    assert "data-natural" not in record_natural_sizes(html)


def test_a_file_that_is_not_an_image_gets_no_size(media):
    (media.directory / "x.svg").write_text("<svg/>")
    (media.directory / "t.png").write_bytes(b"\x89PNG not really")

    for name in ("x.svg", "t.png"):
        assert "data-natural" not in record_natural_sizes(
            _img(f"{media.url}{name}")
        )


def test_a_size_left_from_an_earlier_src_is_removed(media):
    stale = (
        '<img src="https://elsewhere.example/a.png" data-natural-width="9" '
        'data-natural-height="9">'
    )

    assert "data-natural" not in record_natural_sizes(sanitize_html(stale))


def test_a_size_typed_by_hand_is_replaced_with_the_files(media):
    media.add("a.png", (50, 60))
    html = sanitize_html(
        f'<img src="{media.url}a.png" data-natural-width="1" '
        'data-natural-height="1">'
    )

    result = record_natural_sizes(html)

    assert result.count("data-natural-width") == 1
    assert 'data-natural-width="50" data-natural-height="60"' in result


def test_text_without_images_is_returned_as_is():
    assert record_natural_sizes("") == ""
    assert record_natural_sizes("<p>a &amp; b</p>") == "<p>a &amp; b</p>"


@pytest.mark.django_db
def test_a_save_records_the_size(media):
    media.add("a.png", (1600, 900))
    post = BlogPostFactory(
        slug="natural-size-save",
        body=f'<p><img src="{media.url}a.png" width="100%"></p>',
    )

    post.refresh_from_db()

    assert 'data-natural-width="1600" data-natural-height="900"' in post.body
    assert 'width="100%"' in post.body


@pytest.mark.django_db
def test_backfill_updates_rows_saved_before_and_is_idempotent(media):
    post = BlogPostFactory(slug="natural-size-backfill", body="<p>x</p>")
    translation = post.translations.first()
    media.add("a.png", (800, 600))
    old = f'<p><img src="{media.url}a.png" width="100%"></p>'
    type(translation).objects.filter(pk=translation.pk).update(body=old)

    dry = record_current_schema(rich_text_fields(), dry_run=True)
    translation.refresh_from_db()
    assert translation.body == old
    assert dry["blog.BlogPostTranslation.body"].changed >= 1

    first = record_current_schema(rich_text_fields())
    translation.refresh_from_db()
    assert 'data-natural-width="800"' in translation.body
    assert first["blog.BlogPostTranslation.body"].changed >= 1

    second = record_current_schema(rich_text_fields())
    assert all(tally.changed == 0 for tally in second.values())
