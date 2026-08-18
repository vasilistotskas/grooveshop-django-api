"""Write-side validation of PageSection.props (page_config/schemas.py).

Mirror of the storefront's render-time contracts — these tests pin the
boundary behavior: unknown props and out-of-range values are rejected
with readable errors; valid payloads and empty props pass.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from page_config.schemas import validate_section_props


def test_empty_props_always_valid():
    validate_section_props("hero_carousel", {})
    validate_section_props("hero_carousel", None)


def test_unknown_component_type_is_skipped():
    # Model choices validation owns unknown types; props pass through.
    validate_section_props("not_a_section", {"whatever": 1})


def test_valid_hero_carousel_props():
    validate_section_props(
        "hero_carousel",
        {"images": ["/img/a.png"], "link": "/products/1/thing"},
    )


def test_unknown_prop_rejected():
    with pytest.raises(ValidationError) as exc_info:
        validate_section_props("hero_carousel", {"onClick": "alert(1)"})
    assert "unknown prop" in str(exc_info.value)


def test_link_scheme_enforced():
    with pytest.raises(ValidationError):
        validate_section_props("hero_carousel", {"link": "javascript:alert(1)"})
    validate_section_props("hero_carousel", {"link": "https://ok.example"})


def test_int_ranges_enforced():
    with pytest.raises(ValidationError):
        validate_section_props("featured_products", {"page_size": 999})
    with pytest.raises(ValidationError):
        validate_section_props("featured_products", {"page_size": True})
    validate_section_props("featured_products", {"page_size": 8})


def test_cta_banner_background_must_be_hex():
    with pytest.raises(ValidationError):
        validate_section_props(
            "cta_banner", {"background_color": "url(javascript:1)"}
        )
    validate_section_props("cta_banner", {"background_color": "#112233"})


def test_testimonials_items_shape():
    validate_section_props(
        "testimonials",
        {"items": [{"name": "A", "text": "Great"}]},
    )
    with pytest.raises(ValidationError):
        validate_section_props(
            "testimonials",
            {"items": [{"name": "A", "text": "x", "onClick": "evil"}]},
        )


def test_spacer_height_enum():
    validate_section_props("spacer", {"height": "lg"})
    with pytest.raises(ValidationError):
        validate_section_props("spacer", {"height": "huge"})


# ---------------------------------------------------------------------------
# Navigation items validation
# ---------------------------------------------------------------------------


def test_navigation_header_flat_links_valid():
    from page_config.schemas import validate_navigation_items

    validate_navigation_items(
        "header",
        [
            {"label": "Home", "to": "/", "icon": "i-heroicons-home"},
            {"label": "Docs", "href": "https://docs.example"},
        ],
    )


def test_navigation_link_requires_exactly_one_target():
    from page_config.schemas import validate_navigation_items

    with pytest.raises(ValidationError):
        validate_navigation_items("header", [{"label": "X"}])
    with pytest.raises(ValidationError):
        validate_navigation_items(
            "header",
            [{"label": "X", "to": "/a", "href": "https://b.example"}],
        )


def test_navigation_rejects_bad_schemes_and_icons():
    from page_config.schemas import validate_navigation_items

    with pytest.raises(ValidationError):
        validate_navigation_items(
            "header", [{"label": "X", "href": "javascript:alert(1)"}]
        )
    with pytest.raises(ValidationError):
        validate_navigation_items(
            "header",
            [{"label": "X", "to": "/a", "icon": "<svg onload=x>"}],
        )


def test_navigation_footer_columns_shape():
    from page_config.schemas import validate_navigation_items

    validate_navigation_items(
        "footer",
        [
            {
                "label": "Help",
                "icon": "i-heroicons-chat-bubble-left-right",
                "children": [{"label": "Contact", "to": "/contact"}],
            }
        ],
    )
    with pytest.raises(ValidationError):
        validate_navigation_items("footer", [{"label": "Help", "children": []}])
