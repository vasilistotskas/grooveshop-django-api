"""How the editor groups texts and offers their placeholders."""

from __future__ import annotations

import pytest

from texts.areas import (
    ALL_AREAS,
    COMMON,
    area_by_code,
    area_of,
    normalise_namespace,
)
from texts.editor import Filters, chips_for, row_id
from texts.storefront import MessageShape


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ("components/Chrome/Footer", "Chrome/Footer"),
        ("components/variants/webside/Chrome/Footer", "Chrome/Footer"),
        ("components/Chrome/variants/webside/Footer", "Chrome/Footer"),
        (
            "components/PageSection/variants/delta_sigma/Hero",
            "PageSection/Hero",
        ),
        ("layouts/auth-split", "layouts/auth-split"),
        ("global", "global"),
    ],
)
def test_a_designs_own_copy_is_named_like_the_shared_one(namespace, expected):
    assert normalise_namespace(namespace) == expected


@pytest.mark.parametrize(
    ("namespace", "key", "area"),
    [
        ("components/Chrome/Navbar", "x", "header"),
        ("components/Chrome/FooterNewsletter", "x", "footer"),
        ("components/variants/webside/Footer", "x", "footer"),
        ("components/Product/Card", "x", "product"),
        ("components/Products/Filters", "x", "product"),
        ("components/Checkout/TermsConsent", "x", "checkout"),
        ("components/Storefront/Cart", "x", "cart"),
        ("components/Account/Login", "x", "account"),
        ("layouts/auth-split", "x", "account"),
        ("components/Cookie/Control", "x", "legal"),
        ("components/PageSection/Faq", "x", "home"),
        ("components/Blog/Card", "x", "blog"),
        ("components/Search/Bar", "x", "search"),
        ("global", "footer.copyright", "footer"),
        ("global", "cart_empty", "cart"),
        ("global", "two_factor.title", "account"),
        ("global", "unmapped_thing", COMMON),
        ("components/Button/Primary", "x", COMMON),
    ],
)
def test_a_text_belongs_to_the_first_area_that_claims_it(namespace, key, area):
    assert area_of(namespace, key).code == area


def test_every_area_has_a_unique_code_and_a_title():
    codes = [area.code for area in ALL_AREAS]

    assert len(codes) == len(set(codes))
    assert all(str(area.title) for area in ALL_AREAS)
    assert area_by_code("footer") is not None
    assert area_by_code("nope") is None


def test_a_preview_path_is_a_storefront_path():
    for area in ALL_AREAS:
        assert area.preview_path is None or area.preview_path.startswith("/")


@pytest.mark.parametrize(
    ("params", "tokens"),
    [
        (["named:name"], ["{name}"]),
        (["list:0", "list:1"], ["{0}", "{1}"]),
        (["linked:global.brand"], ["@:global.brand"]),
        (["linked:global.brand|upper"], ["@.upper:global.brand"]),
        (["named:n", "linked:a"], ["{n}", "@:a"]),
        ([], []),
        (["mystery:x"], []),
    ],
)
def test_placeholders_become_the_text_that_inserts_them(params, tokens):
    chips = chips_for(MessageShape(params=tuple(params), plural_cases=1))

    assert [chip.token for chip in chips] == tokens


def test_no_shape_has_no_chips():
    assert chips_for(None) == ()


def test_filters_are_read_from_the_query_and_round_trip():
    filters = Filters.from_query(
        {"q": "  hello ", "area": "footer", "changed": "on", "missing": "0"}
    )

    assert filters == Filters(q="hello", area="footer", changed=True)
    assert filters.as_query() == {
        "q": "hello",
        "area": "footer",
        "changed": "1",
    }


def test_an_unknown_area_in_the_query_is_dropped():
    assert Filters.from_query({"area": "nope"}).area == ""


def test_a_row_id_is_stable_and_distinguishes_texts():
    assert row_id("a", "b") == row_id("a", "b")
    assert row_id("a", "b") != row_id("a", "c")
    assert row_id("ab", "") != row_id("a", "b")
    assert row_id("a", "b").startswith("text-")
