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
from core.storefront import StorefrontUnavailable
from tests.utils.builder import (
    HASH,
    default_blocks,
    default_document_kinds,
    document,
    hero,
    section,
)

CATALOGUE = parse_catalogue(
    {
        "hash": HASH,
        "blocks": default_blocks(),
        "documentKinds": default_document_kinds(),
        "templates": ["home", "about", "contact", "product"],
    }
)


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


def test_an_older_version_is_outdated_even_when_accepts_names_only_the_current():
    blocks = default_blocks()
    blocks[0]["accepts"] = [blocks[0]["version"]]
    catalogue = parse_catalogue(
        {"hash": HASH, "blocks": blocks, "templates": ["home"]}
    )

    issues, _ = validate_document(
        key="template:home",
        data=document(("a", section("hero@1", {"title": "x"}))),
        i18n={},
        catalogue=catalogue,
        feature_on=lambda name: True,
    )

    assert codes(issues) == {"/sections/a/type": "block_version_outdated"}


class TestDocumentRootSettings:
    def doc(self, **root):
        return {**document(("a", hero())), "settings": root}

    def test_a_page_may_carry_seo_fields_and_they_are_translatable(self):
        issues, validated = run(
            self.doc(seoTitle="Home", seoDescription="Welcome"),
            i18n={"en": {"@document": {"/seoTitle": "Home page"}}},
        )

        assert issues == []
        assert validated is not None
        assert validated.data["settings"]["seoTitle"] == "Home"
        assert validated.i18n["en"]["@document"] == {"/seoTitle": "Home page"}

    def test_settings_are_optional(self):
        assert run(document(("a", hero())))[0] == []

    @pytest.mark.parametrize(
        ("root", "path", "code"),
        [
            ({"seoTitle": "x" * 71}, "/settings/seoTitle", "invalid_settings"),
            ({"other": 1}, "/settings", "invalid_settings"),
            ("nope", "/settings", "invalid_document"),
        ],
    )
    def test_they_are_held_to_the_catalogues_schema(self, root, path, code):
        data = {**document(("a", hero())), "settings": root}

        issues, _ = run(data)

        assert codes(issues)[path] == code

    def test_a_kind_the_catalogue_gives_no_root_gets_none(self):
        issues, _ = run(
            {
                **document(("a", section("announcement@1", {"text": "x"}))),
                "settings": {},
            },
            key="group:header",
        )

        assert codes(issues) == {"/settings": "document_settings_not_allowed"}

    def test_a_root_setting_is_translated_before_the_default_language_has_one(
        self,
    ):
        # The editor drops an empty ``settings``; the English title may
        # still come first.
        issues, _ = run(
            document(("a", hero())),
            i18n={"en": {"@document": {"/seoTitle": "x"}}},
        )

        assert codes(issues) == {}

    def test_that_translation_is_checked_like_the_default(self):
        issues, _ = run(
            document(("a", hero())),
            i18n={"en": {"@document": {"/seoTitle": "x" * 71}}},
        )

        assert codes(issues) == {
            "/i18n/en/@document/seoTitle": "invalid_settings"
        }

    def test_a_kind_without_root_settings_still_refuses_a_root_overlay(self):
        kinds = default_document_kinds()
        del kinds["template"]
        catalogue = parse_catalogue(
            {
                "hash": HASH,
                "blocks": default_blocks(),
                "documentKinds": kinds,
                "templates": ["home"],
            }
        )

        issues, _ = validate_document(
            key="template:home",
            data=document(("a", hero())),
            i18n={"en": {"@document": {"/seoTitle": "x"}}},
            catalogue=catalogue,
            feature_on=lambda name: True,
        )

        assert codes(issues) == {"/i18n/en/@document": "unknown_node"}

    def test_an_overlay_value_is_checked_like_the_default(self):
        issues, _ = run(
            self.doc(seoTitle="Home"),
            i18n={"en": {"@document": {"/seoTitle": "x" * 71}}},
        )

        assert codes(issues) == {
            "/i18n/en/@document/seoTitle": "invalid_settings"
        }

    def test_an_untranslatable_root_setting_is_refused(self):
        blocks = default_blocks()
        kinds = default_document_kinds()
        kinds["page"]["translatable"] = ["/seoTitle"]
        catalogue = parse_catalogue(
            {"hash": HASH, "blocks": blocks, "documentKinds": kinds}
        )

        issues, _ = validate_document(
            key="page:about",
            data={
                **document(("a", section("banner@1", {"text": "x"}))),
                "settings": {"seoKeywords": "k"},
            },
            i18n={"en": {"@document": {"/seoKeywords": "y"}}},
            catalogue=catalogue,
            feature_on=lambda name: True,
        )

        assert codes(issues) == {
            "/i18n/en/@document/seoKeywords": "untranslatable_setting"
        }


class TestWholeValueTranslations:
    def test_an_array_setting_is_replaced_whole_and_checked_as_a_whole(self):
        base = document(("a", hero(bullets=["one", "two"])))

        ok, validated = run(
            base, i18n={"en": {"a": {"/bullets": ["uno", "dos", "tres"]}}}
        )
        too_long = run(
            base, i18n={"en": {"a": {"/bullets": ["a", "b", "c", "d"]}}}
        )[0]
        item = run(base, i18n={"en": {"a": {"/bullets": ["x" * 21]}}})[0]

        assert ok == []
        assert validated is not None
        assert validated.i18n["en"]["a"]["/bullets"] == ["uno", "dos", "tres"]
        assert codes(too_long) == {"/i18n/en/a/bullets": "invalid_settings"}
        assert codes(item) == {"/i18n/en/a/bullets": "invalid_settings"}


def test_a_blog_category_is_a_numeric_reference():
    issues, validated = run(document(("a", hero(blogCategoryId=4))))
    bad, _ = run(document(("a", hero(blogCategoryId="4"))))

    assert issues == []
    assert validated is not None
    assert validated.refs == {
        "blogCategory": {"4": ["/sections/a/settings/blogCategoryId"]}
    }
    assert "/sections/a/settings/blogCategoryId" in codes(bad)


def test_a_template_must_be_a_page_type_the_storefront_has():
    issues, validated = run(document(("a", hero())), key="template:nowhere")

    assert validated is None
    assert codes(issues) == {"": "unknown_template"}
    # A custom page is not a template: any slug goes.
    banner = document(("a", section("banner@1", {"text": "x"})))
    assert run(banner, key="page:nowhere")[0] == []


class TestLinkReferences:
    def ref(self, kind, ident):
        return {"ref": {"kind": kind, "id": ident}}

    @pytest.mark.parametrize(
        ("kind", "ident"),
        [
            ("product", 7),
            ("category", 3),
            ("blogCategory", 4),
            ("blogPost", 9),
            ("page", "kelari"),
            ("contentPage", "privacy-policy"),
        ],
    )
    def test_each_kind_is_recorded_at_the_links_own_pointer(self, kind, ident):
        issues, validated = run(
            document(("a", hero(link=self.ref(kind, ident))))
        )

        assert issues == []
        assert validated is not None
        assert validated.refs == {
            kind: {str(ident): ["/sections/a/settings/link"]}
        }

    def test_links_in_a_list_are_each_recorded(self):
        issues, validated = run(
            document(
                (
                    "a",
                    hero(
                        cards=[
                            {"link": self.ref("product", 1)},
                            {"link": {"route": "/offers"}},
                            {"link": self.ref("product", 2)},
                        ]
                    ),
                )
            )
        )

        assert issues == []
        assert validated is not None
        assert validated.refs == {
            "product": {
                "1": ["/sections/a/settings/cards/0/link"],
                "2": ["/sections/a/settings/cards/2/link"],
            }
        }

    def test_an_href_and_a_route_are_not_references(self):
        issues, validated = run(
            document(
                ("a", hero(link={"href": "https://example.com"})),
                ("b", hero(link={"route": "/products"})),
            )
        )

        assert issues == []
        assert validated is not None
        assert validated.refs == {}

    @pytest.mark.parametrize(
        "link",
        [
            {"ref": {"kind": "product", "id": "7"}},
            {"ref": {"kind": "product", "id": True}},
            {"ref": {"kind": "product", "id": 0}},
            {"ref": {"kind": "blogPost", "id": -1}},
            {"ref": {"kind": "page", "id": 5}},
            {"ref": {"kind": "contentPage", "id": ""}},
            {"ref": {"kind": "page", "id": "x" * 65}},
        ],
    )
    def test_an_id_that_is_not_the_shape_of_its_kind_is_refused(self, link):
        issues, _ = run(document(("a", hero(link=link))))

        assert "/sections/a/settings/link" in codes(issues)
        assert codes(issues)["/sections/a/settings/link"] in {
            "invalid_reference",
            "invalid_settings",
        }

    def test_a_kind_that_is_not_a_reference_kind_is_refused(self):
        # The catalogue's schema refuses it first; what must hold even for a
        # schema that does not is that it never reaches the existence check.
        blocks = default_blocks()
        blocks[0]["schema"]["properties"]["link"] = {"type": "object"}
        catalogue = parse_catalogue(
            {
                "hash": HASH,
                "blocks": blocks,
                "documentKinds": default_document_kinds(),
                "templates": ["home"],
            }
        )

        issues, validated = validate_document(
            key="template:home",
            data=document(("a", hero(link=self.ref("order", 5)))),
            i18n={},
            catalogue=catalogue,
            feature_on=lambda name: True,
        )

        assert validated is None
        assert codes(issues) == {
            "/sections/a/settings/link": "invalid_reference"
        }

    def test_a_link_is_the_same_in_every_language(self):
        issues, _ = run(
            document(("a", hero(link=self.ref("product", 1)))),
            i18n={"en": {"a": {"/link": self.ref("product", 2)}}},
        )

        assert codes(issues) == {"/i18n/en/a/link": "untranslatable_setting"}


class TestNestedTranslatablesBesideALink:
    """``features_grid``'s shape: text leaves of an object are translated,
    the link in the same object is not."""

    CATALOGUE = parse_catalogue(
        {
            "hash": HASH,
            "blocks": [
                {
                    "type": "grid",
                    "version": 1,
                    "accepts": [],
                    "schema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "text": {"type": "string"},
                                    "ctaLink": {"type": "object"},
                                },
                            }
                        },
                    },
                    "translatable": ["/prompt/title", "/prompt/text"],
                    "linkRefs": ["/prompt/ctaLink"],
                    "kinds": ["section"],
                }
            ],
            "documentKinds": default_document_kinds(),
            "templates": ["home"],
        }
    )
    DOCUMENT = document(
        (
            "a",
            section(
                "grid@1",
                {
                    "prompt": {
                        "title": "Τίτλος",
                        "text": "Κείμενο",
                        "ctaLink": {"ref": {"kind": "product", "id": 2}},
                    }
                },
            ),
        )
    )

    def run(self, i18n):
        return validate_document(
            key="template:home",
            data=self.DOCUMENT,
            i18n=i18n,
            catalogue=self.CATALOGUE,
            feature_on=lambda name: True,
        )

    def test_the_text_leaves_take_an_overlay_at_their_nested_pointers(self):
        overlay = {
            "en": {"a": {"/prompt/title": "Title", "/prompt/text": "Text"}}
        }

        issues, validated = self.run(overlay)

        assert issues == []
        assert validated is not None
        assert validated.i18n == overlay
        assert validated.refs == {
            "product": {"2": ["/sections/a/settings/prompt/ctaLink"]}
        }

    def test_the_link_beside_them_is_refused_in_an_overlay(self):
        overlay = {
            "en": {
                "a": {"/prompt/ctaLink": {"ref": {"kind": "product", "id": 2}}}
            }
        }

        issues, _ = self.run(overlay)

        assert codes(issues) == {
            "/i18n/en/a/prompt/ctaLink": "untranslatable_setting"
        }

    def test_the_whole_object_is_not_translatable(self):
        issues, _ = self.run({"en": {"a": {"/prompt": {"title": "T"}}}})

        assert codes(issues) == {"/i18n/en/a/prompt": "untranslatable_setting"}


class TestTheCatalogueReadsLinks:
    def block(self, **extra):
        return {
            "hash": HASH,
            "blocks": [
                {
                    "type": "cta",
                    "version": 1,
                    "accepts": [],
                    "schema": {"type": "object"},
                    "kinds": ["section"],
                    "linkRefs": [],
                    **extra,
                }
            ],
        }

    def test_a_block_may_declare_links(self):
        catalogue = parse_catalogue(self.block(linkRefs=["/link", "/a/*/link"]))

        assert catalogue.get("cta").link_refs == ("/link", "/a/*/link")

    def test_a_block_without_links_states_none(self):
        assert parse_catalogue(self.block()).get("cta").link_refs == ()

    def test_a_block_that_leaves_the_key_out_is_refused(self):
        payload = self.block()
        del payload["blocks"][0]["linkRefs"]

        with pytest.raises(StorefrontUnavailable):
            parse_catalogue(payload)

    def test_a_root_that_leaves_the_key_out_is_refused(self):
        kinds = default_document_kinds()
        del kinds["page"]["linkRefs"]

        with pytest.raises(StorefrontUnavailable):
            parse_catalogue(
                {"hash": HASH, "blocks": [], "documentKinds": kinds}
            )

    @pytest.mark.parametrize(
        ("link_refs", "translatable"),
        [
            (["/link"], ["/link", "/title"]),
            # A link under a translatable setting, and a translatable
            # setting inside a link.
            (["/prompt/ctaLink"], ["/prompt"]),
            (["/link"], ["/link/label"]),
            # Wildcards count, in either pattern.
            (["/cards/*/link"], ["/cards/0/link"]),
            (["/cards/0/link"], ["/cards/*"]),
            (["/cards/*/link"], ["/cards/*/link/x"]),
        ],
    )
    def test_a_link_cannot_be_translatable_or_inside_something_that_is(
        self, link_refs, translatable
    ):
        with pytest.raises(StorefrontUnavailable) as caught:
            parse_catalogue(
                self.block(linkRefs=link_refs, translatable=translatable)
            )

        assert "cannot be translatable" in str(caught.value.__cause__)

    def test_a_link_beside_translatable_settings_is_fine(self):
        catalogue = parse_catalogue(
            self.block(
                linkRefs=["/prompt/ctaLink", "/cards/*/link"],
                translatable=["/prompt/text", "/cards/*/title", "/title"],
            )
        )

        assert catalogue.get("cta").link_refs == (
            "/prompt/ctaLink",
            "/cards/*/link",
        )
