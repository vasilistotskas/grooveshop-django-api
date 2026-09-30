"""Every section's props contract is published, camelized, in the
OpenAPI document the storefront generates its parser from."""

from __future__ import annotations

from page_config.openapi import component_name
from page_config.section_schemas import SECTION_PROPS


def _keys(schema, found=None):
    """Every property name at every depth."""
    found = set() if found is None else found
    if isinstance(schema, dict):
        found.update(schema.get("properties", {}))
        for value in schema.values():
            _keys(value, found)
    elif isinstance(schema, list):
        for value in schema:
            _keys(value, found)
    return found


def test_every_section_type_is_published(openapi_schema):
    components = openapi_schema["components"]["schemas"]
    missing = [
        component_name(component_type)
        for component_type in SECTION_PROPS
        if component_name(component_type) not in components
    ]
    assert missing == []


def test_published_keys_are_camelized_at_every_depth(openapi_schema):
    component = openapi_schema["components"]["schemas"][
        "PageSectionHeroCarouselProps"
    ]
    keys = _keys(component)
    assert {"autoplayMs", "mobileImages"} <= keys
    # Inside ``slides[]``: the camelize hook never reaches this deep.
    assert {"imageUrl", "secondaryCtaLink"} <= keys
    assert not {key for key in keys if "_" in key}
    slide = component["properties"]["slides"]["items"]
    assert slide["required"] == ["imageUrl"]
