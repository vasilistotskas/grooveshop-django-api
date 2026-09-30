"""Publish the section contract in the OpenAPI document.

Each section type's props schema becomes a named component,
``PageSection<Type>Props`` (e.g. ``PageSectionHeroBannerProps``),
camelized as the API sends the data, so the storefront generates its
render-time parser from Django's contract instead of re-writing it.
"""

from __future__ import annotations

from typing import Any

from core.json_schema import wire_schema
from page_config.section_schemas import SECTION_PROPS, props_schema


def component_name(component_type: str) -> str:
    pascal = "".join(part.capitalize() for part in component_type.split("_"))
    return f"PageSection{pascal}Props"


def publish_section_schemas(
    result: dict[str, Any], generator: Any, request: Any, public: bool
) -> dict[str, Any]:
    schemas = result.setdefault("components", {}).setdefault("schemas", {})
    for component_type in SECTION_PROPS:
        schemas[component_name(component_type)] = wire_schema(
            props_schema(component_type)
        )
    return result
