"""``media_migration.migrate_document``: a value it cannot place is refused,
never dropped. The old setting leaves the document either way, so a value of
a shape the walk does not expect would otherwise go without a word."""

from __future__ import annotations

import pytest

from builder.document_rewrite import DocumentRefused
from builder.media_migration import migrate_document

IMAGE = "/img/a.png"
ASSETS = {IMAGE: 7}


def one(block: str, **settings) -> dict:
    return {
        "sections": {"a": {"type": block, "settings": settings}},
        "order": ["a"],
    }


def settings_after(document: dict) -> dict:
    migrated, _overlay, _moved = migrate_document(document, {}, ASSETS)
    return migrated["sections"]["a"]["settings"]


def test_a_list_takes_its_images_and_drops_what_is_empty():
    document = one("hero_carousel@1", images=[IMAGE, None, "", "  "])

    assert settings_after(document) == {"artworks": [7]}


@pytest.mark.parametrize(
    "images",
    [IMAGE, {"0": IMAGE}, 7, [IMAGE, 7], [IMAGE, [IMAGE]], [{"src": IMAGE}]],
)
def test_a_list_of_another_shape_is_refused(images):
    with pytest.raises(DocumentRefused):
        settings_after(one("hero_carousel@1", images=images))


@pytest.mark.parametrize("value", [[IMAGE], 7, {"src": IMAGE}, True])
def test_a_single_value_of_another_shape_is_refused(value):
    with pytest.raises(DocumentRefused):
        settings_after(one("hero_banner@2", imageUrl=value))


def test_an_empty_single_value_leaves_no_image():
    assert settings_after(one("hero_banner@2", imageUrl="", heading="H")) == {
        "heading": "H"
    }
