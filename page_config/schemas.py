"""Write-side validation for ``PageSection.props``.

Plain-Python mirror of the storefront's render-time contracts
(``shared/pageSections.ts`` in the Nuxt repo) — keep the two in sync
when adding section types. The Nuxt ``safeParse`` remains the
render-time authority (bad historical props degrade to component
defaults there); this stops NEW bad props at the admin/API boundary
with a readable error.

Django stores props snake_case; the API layer camelizes on the wire,
so keys here are snake_case.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LINK_RE = re.compile(r"^(/|https://)")


def _is_str(value, max_length: int) -> bool:
    return isinstance(value, str) and len(value) <= max_length


def _is_int(value, lo: int, hi: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (lo <= value <= hi)
    )


def _check_testimonial_items(value) -> str | None:
    if not isinstance(value, list) or len(value) > 20:
        return "items: must be a list of at most 20 entries"
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"items[{i}]: must be an object"
        item = {str(k): v for k, v in raw.items()}
        if not _is_str(item.get("name", ""), 100):
            return f"items[{i}].name: must be a string (max 100)"
        if not _is_str(item.get("text", ""), 1000):
            return f"items[{i}].text: must be a string (max 1000)"
        avatar = item.get("avatar")
        if avatar is not None and not _is_str(avatar, 1000):
            return f"items[{i}].avatar: must be a string (max 1000)"
        unknown = set(item) - {"name", "text", "avatar"}
        if unknown:
            return f"items[{i}]: unknown keys {sorted(unknown)}"
    return None


# key -> validator(value) returning an error string or None
_VALIDATORS: dict[str, dict] = {
    "hero_banner": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "subheading": lambda v: None if _is_str(v, 500) else "string ≤500",
        "image_url": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "overlay_opacity": lambda v: (
            None
            if isinstance(v, (int, float)) and 0 <= v <= 1
            else "number between 0 and 1"
        ),
    },
    "hero_carousel": {
        "images": lambda v: (
            None
            if isinstance(v, list)
            and len(v) <= 10
            and all(_is_str(s, 1000) for s in v)
            else "list of ≤10 strings"
        ),
        "link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
    },
    "products_slider": {
        "page_size": lambda v: None if _is_int(v, 1, 24) else "int 1–24",
    },
    "products_grid": {
        "page_size": lambda v: None if _is_int(v, 1, 48) else "int 1–48",
    },
    "featured_products": {
        "page_size": lambda v: None if _is_int(v, 1, 24) else "int 1–24",
        "columns": lambda v: None if _is_int(v, 1, 6) else "int 1–6",
    },
    "product_categories": {},
    "blog_categories": {},
    "blog_posts_carousel": {
        "count": lambda v: None if _is_int(v, 1, 12) else "int 1–12",
    },
    "blog_posts_grid": {
        "count": lambda v: None if _is_int(v, 1, 24) else "int 1–24",
    },
    "blog_posts_list": {
        "page_size": lambda v: None if _is_int(v, 1, 24) else "int 1–24",
    },
    "recently_viewed": {},
    "rich_text": {
        "content": lambda v: None if _is_str(v, 20000) else "string ≤20000",
    },
    "cta_banner": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "description": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "button_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "button_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "background_color": lambda v: (
            None if isinstance(v, str) and _HEX_RE.match(v) else "#RRGGBB hex"
        ),
    },
    "newsletter_signup": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "description": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "placeholder": lambda v: None if _is_str(v, 100) else "string ≤100",
    },
    "testimonials": {
        "items": _check_testimonial_items,
    },
    "spacer": {
        "height": lambda v: (
            None if v in ("sm", "md", "lg", "xl") else "one of sm/md/lg/xl"
        ),
    },
    "divider": {},
    "loyalty_hero": {},
    "search_bar": {},
}


def validate_section_props(component_type: str, props: object) -> None:
    """Raise ``ValidationError`` when ``props`` doesn't fit the section
    contract. Unknown component types are the model field's problem
    (choices validation) — skipped here."""
    validators = _VALIDATORS.get(component_type)
    if validators is None or props in (None, {}):
        return
    if not isinstance(props, dict):
        raise ValidationError("props must be a JSON object.")

    errors: list[str] = []
    for key, value in props.items():
        check = validators.get(key)
        if check is None:
            errors.append(f"{key}: unknown prop for {component_type}")
            continue
        problem = check(value)
        if problem:
            errors.append(f"{key}: {problem}")

    if errors:
        raise ValidationError(
            f"Invalid props for {component_type}: " + "; ".join(errors)
        )
