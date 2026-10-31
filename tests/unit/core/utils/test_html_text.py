from __future__ import annotations

import pytest

from blog.factories.post import BlogPostFactory
from core.utils.html_text import visible_text
from product.factories.product import ProductFactory


def test_a_space_stands_where_a_tag_was():
    assert visible_text("<p>one</p><p>two<br>three</p>") == "one two three"


def test_entities_are_decoded_and_whitespace_collapsed():
    assert (
        visible_text("<p>Tom&nbsp;&amp;\n  Jerry &lt;3</p>") == "Tom & Jerry <3"
    )


def test_attribute_text_is_not_text():
    html = (
        '<p style="color: #3364ff" class="lead"><img src="/media/x/a.png" '
        'alt="a" data-natural-width="1200" data-natural-height="800">hello</p>'
    )

    assert visible_text(html) == "hello"


def test_none_and_empty():
    assert visible_text(None) is None
    assert visible_text("") == ""
    assert visible_text("<p>&nbsp;</p>") == ""


@pytest.mark.django_db
def test_the_blog_index_holds_the_words_of_the_body():
    from blog.models.post import BlogPostTranslation

    post = BlogPostFactory(
        slug="index-text",
        body=(
            '<p class="lead">Καλημέρα</p><img src="/media/x/uploads/a.png" '
            'alt="a" width="100%">'
        ),
    )
    translation = post.translations.get(language_code="el")

    document = translation.meili_serialize()

    assert document["body"] == "Καλημέρα"
    assert document["body_greeklish"] == "kalimera"
    assert "media" not in document["body"]
    assert BlogPostTranslation.MeiliMeta.displayed_fields.count("body") == 1


@pytest.mark.django_db
def test_the_product_index_holds_the_words_of_the_description():
    product = ProductFactory()
    translation = product.translations.get(language_code="el")
    translation.description = '<div style="x:y"><p>Μπλε</p></div>'

    document = translation.meili_serialize()

    assert document["description"] == "Μπλε"
    assert document["description_greeklish"] == "mple"
