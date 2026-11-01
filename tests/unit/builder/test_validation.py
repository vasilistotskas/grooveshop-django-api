"""``builder.validation``: every rule, as a JSON pointer and a stable code.

The catalogue is the storefront's, so these build one from the fake at
``tests/utils/builder.py`` and run the validator on it without a database
(references are checked separately, at publish).
"""

from __future__ import annotations

from typing import Any

import pytest

from builder.storefront import parse_catalogue
from builder.validation import validate_document
from tests.utils.builder import HASH, default_blocks, document, hero, section

CATALOGUE = parse_catalogue({"hash": HASH, "blocks": default_blocks()})


def run(
    data: Any,
    *,
    key: str = "template:home",
    i18n: Any = None,
    feature_on=lambda name: True,
):
    return validate_document(
        key=key,
        data=data,
        i18n={} if i18n is None else i18n,
        catalogue=CATALOGUE,
        feature_on=feature_on,
    )


def codes(issues) -> dict[str, str]:
    return {issue.path: issue.code for issue in issues}


def test_a_valid_document_reports_what_it_uses():
    issues, validated = run(
        document(
            ("hero_1", hero(productId=7, categoryId=3)),
            ("banner_1", section("banner@1", {"text": "Hi"})),
        )
    )

    assert issues == []
    assert validated is not None
    assert validated.block_types == ["banner@1", "hero@2"]
    assert validated.refs == {
        "product": {"7": ["/sections/hero_1/settings/productId"]},
        "category": {"3": ["/sections/hero_1/settings/categoryId"]},
    }
    assert len(validated.content_hash) == 64


def test_the_hash_does_not_depend_on_key_order():
    one = run(document(("a", hero("X", productId=1))))[1]
    other = run(
        {
            "order": ["a"],
            "sections": {
                "a": {
                    "settings": {"productId": 1, "title": "X"},
                    "type": "hero@2",
                }
            },
        }
    )[1]

    assert one is not None and other is not None
    assert one.content_hash == other.content_hash


@pytest.mark.parametrize(
    ("data", "path", "code"),
    [
        ("nope", "", "invalid_document"),
        ({"sections": {}, "order": [], "x": 1}, "/x", "invalid_document"),
        ({"sections": []}, "/sections", "invalid_document"),
        (
            {"sections": {"a": hero()}, "order": []},
            "/order",
            "order_mismatch",
        ),
        (
            {"sections": {"a": hero()}, "order": ["a", "a"]},
            "/order",
            "order_mismatch",
        ),
        (
            {"sections": {"a b": hero()}, "order": ["a b"]},
            "/sections/a b",
            "invalid_node_id",
        ),
        (
            document(("a", {"type": "hero@2"})),
            "/sections/a/settings",
            "invalid_document",
        ),
        (
            document(("a", {**hero(), "disabled": "yes"})),
            "/sections/a/disabled",
            "invalid_document",
        ),
        (
            document(("a", {**hero(), "extra": 1})),
            "/sections/a/extra",
            "invalid_document",
        ),
    ],
)
def test_the_shape_is_refused(data, path, code):
    issues, validated = run(data)

    assert validated is None
    assert codes(issues)[path] == code


def test_a_duplicate_id_across_sections_and_blocks_is_refused():
    data = document(
        (
            "a",
            {
                **hero(),
                "blocks": {"a": section("slide@1")},
                "blockOrder": ["a"],
            },
        )
    )

    issues, _ = run(data)

    assert codes(issues) == {"/sections/a/blocks/a": "duplicate_node_id"}


def test_block_and_blockorder_go_together_and_list_every_block():
    only_blocks = document(("a", {**hero(), "blocks": {}}))
    short_order = document(
        (
            "a",
            {
                **hero(),
                "blocks": {"s1": section("slide@1"), "s2": section("slide@1")},
                "blockOrder": ["s1"],
            },
        )
    )

    assert codes(run(only_blocks)[0]) == {"/sections/a": "invalid_document"}
    assert codes(run(short_order)[0]) == {
        "/sections/a/blockOrder": "order_mismatch"
    }


def test_a_setting_that_breaks_the_schema_is_pinned_to_its_pointer():
    issues, _ = run(
        document(
            ("a", hero("x" * 101)),
            ("b", section("hero@2", {"title": "ok", "unknown": 1})),
            ("c", section("hero@2", {})),
        )
    )

    assert codes(issues) == {
        "/sections/a/settings/title": "invalid_settings",
        "/sections/b/settings": "invalid_settings",
        "/sections/c/settings": "invalid_settings",
    }


@pytest.mark.parametrize(
    ("type_", "code"),
    [
        ("hero", "invalid_block_type"),
        ("hero@x", "invalid_block_type"),
        ("nothing@1", "unknown_block_type"),
        ("hero@1", "block_version_outdated"),
        ("hero@9", "unknown_block_version"),
    ],
)
def test_a_type_must_name_the_current_version_of_a_known_block(type_, code):
    issues, validated = run(document(("a", section(type_, {"title": "x"}))))

    assert validated is None
    assert codes(issues) == {"/sections/a/type": code}


def test_a_block_cannot_be_a_section_and_a_section_cannot_be_a_block():
    nested = document(
        (
            "a",
            {
                **hero(),
                "blocks": {"x": hero("inner")},
                "blockOrder": ["x"],
            },
        )
    )

    assert codes(
        run(document(("a", section("slide@1")), ("b", hero())))[0]
    ) == {"/sections/a/type": "not_a_section"}
    assert ("/sections/a/blocks/x/type", "not_a_block") in {
        (issue.path, issue.code) for issue in run(nested)[0]
    }


def test_a_block_may_only_go_where_its_groups_and_templates_say():
    announcement = section("announcement@1", {"text": "Sale"})

    assert run(document(("a", announcement)), key="group:header")[0] == []
    assert codes(run(document(("a", announcement)), key="group:footer")[0]) == {
        "/sections/a/type": "block_not_allowed_here"
    }
    assert codes(run(document(("a", announcement)))[0]) == {
        "/sections/a/type": "block_not_allowed_here"
    }
    # ``hero`` names the template ``home`` only.
    assert run(document(("a", hero())))[0] == []
    assert codes(run(document(("a", hero())), key="template:product")[0]) == {
        "/sections/a/type": "block_not_allowed_here"
    }
    assert codes(run(document(("a", hero())), key="group:header")[0]) == {
        "/sections/a/type": "block_not_allowed_here"
    }
    # A block naming neither goes in a template or a page.
    banner = section("banner@1", {"text": "x"})
    assert run(document(("a", banner)), key="page:about")[0] == []


def test_the_theme_takes_only_blocks_that_name_the_theme_group():
    theme = section("theme@1", {"radius": "lg"})

    assert run(document(("t", theme)), key="theme")[0] == []
    assert codes(run(document(("b", hero())), key="theme")[0]) == {
        "/sections/b/type": "block_not_allowed_here"
    }


def test_a_document_key_must_be_well_formed():
    issues, validated = run(document(), key="Template:Home")

    assert validated is None
    assert codes(issues) == {"": "invalid_key"}


def test_children_must_be_allowed_by_their_parent_and_within_its_limits():
    def with_blocks(*blocks: tuple[str, str]):
        return document(
            (
                "a",
                {
                    **hero(),
                    "blocks": {bid: section(t) for bid, t in blocks},
                    "blockOrder": [bid for bid, _ in blocks],
                },
            )
        )

    assert run(with_blocks(("s1", "slide@1")))[0] == []
    assert codes(run(with_blocks(("c", "column@1")))[0]) == {
        "/sections/a/blocks/c/type": "child_not_allowed"
    }
    too_many = with_blocks(*((f"s{n}", "slide@1") for n in range(4)))
    assert codes(run(too_many)[0]) == {"/sections/a/blocks": "too_many_blocks"}


def test_nesting_is_capped_by_the_store_and_by_the_parent():
    def nest(depth: int) -> dict[str, Any]:
        node: dict[str, Any] = section("item@1", {"label": "leaf"})
        for level in range(depth):
            node = {
                **section("item@1", {"label": f"n{level}"}),
                "blocks": {f"i{level}": node},
                "blockOrder": [f"i{level}"],
            }
        return node

    columns = {
        **section("columns@1"),
        "blocks": {
            "c": {
                **section("column@1"),
                "blocks": {"i": nest(1)},
                "blockOrder": ["i"],
            }
        },
        "blockOrder": ["c"],
    }

    # section(1) > column(2) > item(3) > item(4): one level too deep.
    assert codes(run(document(("s", columns)))[0]) == {
        "/sections/s/blocks/c/blocks/i/blocks": "max_depth_exceeded"
    }
    # ``hero`` allows one level of children, so a slide with a child is out.
    slide = {
        **section("slide@1"),
        "blocks": {"x": section("slide@1")},
        "blockOrder": ["x"],
    }
    deep = document(
        ("h", {**hero(), "blocks": {"s": slide}, "blockOrder": ["s"]})
    )
    assert (
        codes(run(deep)[0])["/sections/h/blocks/s/blocks"]
        == "max_depth_exceeded"
    )


def test_a_type_can_be_limited_per_document():
    three = document(*((f"c{n}", section("columns@1")) for n in range(3)))

    assert codes(run(three)[0]) == {"/sections/c2": "block_limit_exceeded"}


def test_the_store_limits_come_from_settings(settings):
    settings.BUILDER_MAX_SECTIONS_PER_DOCUMENT = 1
    two = document(("a", hero()), ("b", hero()))

    assert codes(run(two)[0]) == {"/sections": "too_many_sections"}

    settings.BUILDER_MAX_DOCUMENT_BYTES = 100
    assert codes(run(document(("a", hero("x" * 90))))[0]) == {
        "": "document_too_large"
    }


def test_a_feature_gated_block_needs_its_feature_on():
    newsletter = document(("n", section("newsletter@1", {"heading": "Join"})))

    assert run(newsletter)[0] == []
    assert codes(run(newsletter, feature_on=lambda name: False)[0]) == {
        "/sections/n/type": "feature_disabled"
    }

    def unknown(name: str) -> bool:
        raise KeyError(name)

    assert codes(run(newsletter, feature_on=unknown)[0]) == {
        "/sections/n/type": "unknown_feature"
    }


def test_references_must_have_the_shape_of_their_kind():
    issues, _ = run(
        document(
            (
                "a",
                hero(
                    productId="7",
                    categoryId=True,
                    pageSlug="",
                    mediaId=-1,
                ),
            )
        )
    )

    assert {
        i.path: i.code for i in issues if i.code == "invalid_reference"
    } == {
        "/sections/a/settings/productId": "invalid_reference",
        "/sections/a/settings/categoryId": "invalid_reference",
        "/sections/a/settings/pageSlug": "invalid_reference",
        "/sections/a/settings/mediaId": "invalid_reference",
    }


def test_rich_text_is_sanitised_and_content_it_would_lose_is_refused():
    issues, validated = run(
        document(
            ("a", hero(body='<p onclick="x()">Hello <b>there</b></p>')),
        )
    )
    assert issues == []
    assert validated is not None
    assert (
        validated.data["sections"]["a"]["settings"]["body"]
        == "<p>Hello <b>there</b></p>"
    )

    refused, _ = run(
        document(
            ("a", hero(body='<iframe src="https://evil.example"></iframe>'))
        )
    )
    assert codes(refused) == {"/sections/a/settings/body": "unsupported_markup"}


def test_the_input_is_never_modified():
    data = document(("a", hero(body="<p onclick='x()'>Hi</p>")))
    before = repr(data)

    run(data)

    assert repr(data) == before


class TestTranslations:
    def test_a_translatable_setting_can_be_overlaid_per_language(self):
        issues, validated = run(
            document(("a", hero("Καλώς")), ("b", hero("Hi"))),
            i18n={"en": {"a": {"/title": "Welcome"}}},
        )

        assert issues == []
        assert validated is not None
        assert validated.i18n == {"en": {"a": {"/title": "Welcome"}}}

    @pytest.mark.parametrize(
        ("i18n", "path", "code"),
        [
            ({"fr": {}}, "/i18n/fr", "unknown_locale"),
            ({"el": {}}, "/i18n/el", "unknown_locale"),
            ({"en": {"zzz": {}}}, "/i18n/en/zzz", "unknown_node"),
            (
                {"en": {"a": {"/productId": 3}}},
                "/i18n/en/a/productId",
                "untranslatable_setting",
            ),
            (
                {"en": {"a": {"/title": "x" * 101}}},
                "/i18n/en/a/title",
                "invalid_settings",
            ),
            ({"en": []}, "/i18n/en", "invalid_i18n"),
            ([], "/i18n", "invalid_i18n"),
        ],
    )
    def test_an_overlay_is_held_to_the_same_rules(self, i18n, path, code):
        issues, _ = run(document(("a", hero("t"))), i18n=i18n)

        assert codes(issues)[path] == code

    def test_translated_rich_text_is_sanitised(self):
        issues, validated = run(
            document(("a", hero("t", body="<p>ελ</p>"))),
            i18n={"en": {"a": {"/body": "<p onclick='x()'>en</p>"}}},
        )

        assert issues == []
        assert validated is not None
        assert validated.i18n["en"]["a"]["/body"] == "<p>en</p>"
