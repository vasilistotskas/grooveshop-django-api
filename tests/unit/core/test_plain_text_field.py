import pytest

from blog.factories.post import BlogPostFactory
from core.fields.plain_text import normalize_plain_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<div>Ένας οδηγός.</div>", "Ένας οδηγός."),
        ("<p>a</p><p>b</p>", "ab"),
        ("plain text", "plain text"),
        ("", ""),
        ("  spaced   out \n text ", "spaced out text"),
        ("Tom &amp; Jerry", "Tom & Jerry"),
        ('<a href="/x">link</a> tail', "link tail"),
        (None, None),
    ],
)
def test_normalize_plain_text(raw, expected):
    assert normalize_plain_text(raw) == expected


@pytest.mark.django_db
def test_seo_description_is_stored_as_plain_text():
    """The value lands inside an HTML attribute, so markup must not survive.

    40% of Webside's posts shipped
    ``<meta name="description" content="<div>…</div>">`` before this.
    """
    post = BlogPostFactory(
        slug="plain-text-seo-description",
        seo_description="<div>Ένας πλήρης οδηγός.</div>",
    )

    post.refresh_from_db()

    assert post.seo_description == "Ένας πλήρης οδηγός."
