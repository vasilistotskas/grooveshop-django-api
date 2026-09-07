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


@pytest.mark.parametrize(
    "component_type",
    [
        "about_content",
        "vision_content",
        "what_is_microlearning",
        "why_microlearning",
    ],
)
def test_brand_content_blocks_take_no_props(component_type):
    # Same shape as divider/loyalty_hero/search_bar: rendering is a
    # per-tenant Nuxt variant component with no configurable props.
    validate_section_props(component_type, {})
    validate_section_props(component_type, None)
    with pytest.raises(ValidationError):
        validate_section_props(component_type, {"heading": "Not allowed"})


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


def test_location_map_props():
    validate_section_props(
        "location_map",
        {
            "embed_url": "https://www.google.com/maps/embed?pb=abc",
            "lat": 40.563156,
            "lng": 22.110502,
            "address": "Fyteia, Veria",
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props(
            "location_map", {"embed_url": "http://insecure.example"}
        )
    with pytest.raises(ValidationError):
        validate_section_props("location_map", {"lat": 400})


def test_business_hours_carries_no_props():
    validate_section_props("business_hours", {})
    with pytest.raises(ValidationError):
        validate_section_props("business_hours", {"schedule": {}})


def test_hero_banner_stats_shape():
    """The proof row under the hero copy."""
    validate_section_props(
        "hero_banner",
        {
            "heading": "Turnkey automation",
            "stats": [
                {"value": "48", "label": "documented projects"},
                {"value": "7", "label": "fields of expertise"},
            ],
        },
    )
    with pytest.raises(ValidationError):
        # A number, not a string: the row prints "50+" and "1.842" as
        # often as a bare integer, so the prop is text.
        validate_section_props("hero_banner", {"stats": [{"value": 48}]})
    with pytest.raises(ValidationError):
        validate_section_props(
            "hero_banner", {"stats": [{"value": "48", "label": ""}]}
        )
    with pytest.raises(ValidationError):
        validate_section_props(
            "hero_banner",
            {"stats": [{"value": "48", "label": "x", "note": "y"}]},
        )
    with pytest.raises(ValidationError):
        validate_section_props(
            "hero_banner", {"stats": [{"value": "1", "label": "x"}] * 5}
        )


def test_hero_banner_stats_errors_name_their_own_key():
    """Not "items" — the shared checker is told which prop it is."""
    with pytest.raises(ValidationError, match="stats"):
        validate_section_props("hero_banner", {"stats": "48"})


def test_page_hero_props():
    """The top of every inner page."""
    validate_section_props(
        "page_hero",
        {
            "eyebrow": "Delta Sigma Energy Telecontrol",
            "heading": "DeSET",
            "standfirst": "Telemetry for renewable plants.",
            "body": "In full compliance with HEDNO's specifications.",
            "cta_text": "Request a quote",
            "cta_link": "/contact",
            "secondary_cta_text": "Compare the systems",
            "secondary_cta_link": "/deset",
            "stats": [{"value": "48", "label": "projects"}],
            "callout": {
                "tone": "warning",
                "title": "Regulatory obligation",
                "text": "Every plant above 400 kW must report.",
                "note": "Law 5106/2024",
            },
            "facts": [
                {"label": "Protocol", "value": "IEC 60870-5-104"},
                {"label": "Threshold", "value": "> 400 kW"},
            ],
        },
    )


def test_page_hero_callout_tone_is_an_enum_not_a_colour():
    """A merchant cannot author a hex into a page."""
    with pytest.raises(ValidationError):
        validate_section_props(
            "page_hero",
            {"callout": {"tone": "#ff0000", "title": "x"}},
        )
    with pytest.raises(ValidationError):
        validate_section_props("page_hero", {"callout": {"text": "no title"}})
    with pytest.raises(ValidationError):
        validate_section_props(
            "page_hero", {"callout": {"title": "x", "colour": "red"}}
        )
    with pytest.raises(ValidationError):
        validate_section_props("page_hero", {"callout": [{"title": "x"}]})


def test_page_hero_facts_are_bounded_and_named():
    with pytest.raises(ValidationError, match="facts"):
        validate_section_props("page_hero", {"facts": [{"label": "x"}]})
    with pytest.raises(ValidationError):
        validate_section_props(
            "page_hero", {"facts": [{"label": "x", "value": "y"}] * 7}
        )


def test_partner_strip_props():
    validate_section_props(
        "partner_strip",
        {
            "label": "We work with",
            "items": [{"name": "ABB"}, {"name": "ODOT", "href": "/info/x"}],
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props("partner_strip", {"items": [{"name": ""}]})
    with pytest.raises(ValidationError):
        validate_section_props(
            "partner_strip",
            {"items": [{"name": "ABB", "href": "javascript:alert(1)"}]},
        )
    with pytest.raises(ValidationError):
        validate_section_props(
            "partner_strip", {"items": [{"name": "ABB", "logo": "x.svg"}]}
        )
    with pytest.raises(ValidationError):
        validate_section_props("partner_strip", {"heading": "We work with"})


def test_features_grid_items_shape():
    validate_section_props(
        "features_grid",
        {
            "heading": "Why us",
            "items": [
                {
                    "title": "Homemade",
                    "text": "Small batches",
                    "icon": "i-lucide-heart",
                }
            ],
            "columns": 3,
            "decor": "gradient_tiles",
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props(
            "features_grid",
            {"items": [{"title": "X", "icon": "<svg onload=x>"}]},
        )
    with pytest.raises(ValidationError):
        validate_section_props("features_grid", {"decor": "sparkles"})
    with pytest.raises(ValidationError):
        validate_section_props(
            "features_grid", {"items": [{"text": "no title"}]}
        )


def test_media_text_band_extras():
    """Eyebrow, footnote, checklist and comparison cards."""
    validate_section_props(
        "media_text",
        {
            "eyebrow": "Mandatory above 400 kW",
            "heading": "DeSET",
            "body": "The Delta Sigma systems comply.",
            "emphasis": "Delta Sigma",
            "note": "Law 5106/2024",
            "bullets": [{"text": "Industrial-grade hardware."}],
            "specs": [
                {
                    "label": "System 01",
                    "name": "ABB",
                    "subtitle": "PM5072-2ETH",
                    "rows": [{"label": "Memory", "value": "8 MB"}],
                }
            ],
        },
    )


def test_media_text_specs_are_bounded_and_typed():
    with pytest.raises(ValidationError):
        # A card without a name has nothing to head the column with.
        validate_section_props("media_text", {"specs": [{"label": "01"}]})
    with pytest.raises(ValidationError):
        validate_section_props(
            "media_text", {"specs": [{"name": "ABB", "logo": "abb.svg"}]}
        )
    with pytest.raises(ValidationError):
        validate_section_props(
            "media_text",
            {"specs": [{"name": "ABB", "rows": [{"label": "Memory"}]}]},
        )
    with pytest.raises(ValidationError):
        validate_section_props(
            "media_text",
            {
                "specs": [
                    {
                        "name": "ABB",
                        "rows": [{"label": "x", "value": "y"}] * 9,
                    }
                ]
            },
        )
    with pytest.raises(ValidationError):
        validate_section_props("media_text", {"specs": [{"name": "ABB"}] * 5})
    with pytest.raises(ValidationError):
        validate_section_props("media_text", {"specs": {"name": "ABB"}})


def test_media_text_bullets_errors_name_their_own_key():
    with pytest.raises(ValidationError, match="bullets"):
        validate_section_props("media_text", {"bullets": [{"title": "x"}]})


def test_media_text_props():
    validate_section_props(
        "media_text",
        {
            "heading": "Our story",
            "body": "Eleven women…",
            "image_url": "/img/story.jpg",
            "image_position": "left",
            "cta_text": "Read more",
            "cta_link": "/about",
            "decor": "orbs",
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props(
            "media_text", {"cta_link": "javascript:alert(1)"}
        )
    with pytest.raises(ValidationError):
        validate_section_props("media_text", {"image_position": "top"})


def test_image_gallery_items_shape():
    validate_section_props(
        "image_gallery",
        {
            "items": [{"src": "/img/a.jpg", "alt": "A", "caption": "cap"}],
            "columns": 3,
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props(
            "image_gallery", {"items": [{"src": "/img/a.jpg"}]}
        )
    with pytest.raises(ValidationError):
        validate_section_props("image_gallery", {"columns": 5})


def test_hero_banner_decor_and_secondary_cta():
    validate_section_props(
        "hero_banner",
        {
            "eyebrow": "Γυναικείος Συνεταιρισμός",
            "heading": "Από τη φύση",
            "decor": "orbs",
            "cta_text": "Η ιστορία μας",
            "cta_link": "/about",
            "secondary_cta_text": "Επικοινωνία",
            "secondary_cta_link": "/contact",
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props("hero_banner", {"decor": "sparkles"})
    with pytest.raises(ValidationError):
        validate_section_props(
            "hero_banner", {"secondary_cta_link": "javascript:x"}
        )


def test_divider_variant():
    validate_section_props("divider", {"variant": "thread"})
    validate_section_props("divider", {"variant": "line"})
    with pytest.raises(ValidationError):
        validate_section_props("divider", {"variant": "dotted"})


def test_story_timeline_items_shape():
    validate_section_props(
        "story_timeline",
        {
            "heading": "Από το χωράφι στο βάζο",
            "items": [
                {
                    "title": "Συγκομιδή",
                    "date": "Άνοιξη",
                    "text": "Μαζεύουμε καρπούς και βότανα.",
                    "icon": "i-heroicons-sun",
                }
            ],
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props("story_timeline", {"items": [{"date": "2020"}]})
    with pytest.raises(ValidationError):
        validate_section_props(
            "story_timeline",
            {"items": [{"title": "X", "icon": "<svg onload=x>"}]},
        )


def test_faq_items_shape():
    validate_section_props(
        "faq",
        {
            "heading": "Συχνές Ερωτήσεις",
            "multiple": True,
            "items": [{"question": "Πού;", "answer": "Στη Φυτειά."}],
        },
    )
    with pytest.raises(ValidationError):
        validate_section_props("faq", {"items": [{"question": "Πού;"}]})
    with pytest.raises(ValidationError):
        validate_section_props("faq", {"multiple": "yes"})
