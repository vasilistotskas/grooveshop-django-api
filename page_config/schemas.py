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
from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import ValidationError

from core.utils.i18n import available_language_codes

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


def _check_items(
    value,
    *,
    max_items: int,
    required: dict[str, int],
    optional: dict[str, int],
    link_keys: frozenset[str] = frozenset(),
    icon_keys: frozenset[str] = frozenset(),
    name: str = "items",
) -> str | None:
    """Shared list-of-objects checker: ``required``/``optional`` map key
    -> max string length; ``icon_keys`` must additionally match the
    ``i-*`` icon pattern. ``name`` heads the error messages, so a prop
    called something other than ``items`` reports its own key."""
    if not isinstance(value, list) or len(value) > max_items:
        return f"{name}: must be a list of at most {max_items} entries"
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"{name}[{i}]: must be an object"
        item = {str(k): v for k, v in raw.items()}
        for key, max_length in required.items():
            entry = item.get(key)
            if not entry or not _is_str(entry, max_length):
                return f"{name}[{i}].{key}: required string (max {max_length})"
        for key, max_length in optional.items():
            entry = item.get(key)
            if entry is not None and not _is_str(entry, max_length):
                return f"{name}[{i}].{key}: must be a string (max {max_length})"
        for key in icon_keys:
            entry = item.get(key)
            if entry is not None and not _ICON_RE.match(str(entry)):
                return f"{name}[{i}].{key}: must be an i-* icon name"
        for key in link_keys:
            entry = item.get(key)
            if entry is not None and not _LINK_RE.match(str(entry)):
                return f"{name}[{i}].{key}: internal path or https URL"
        unknown = set(item) - set(required) - set(optional)
        if unknown:
            return f"{name}[{i}]: unknown keys {sorted(unknown)}"
    return None


def _check_nested_lines(
    value,
    *,
    name: str,
    max_items: int,
    required: dict[str, int],
    optional: dict[str, int],
    lines_key: str,
    max_lines: int,
    line_length: int,
    icon_keys: frozenset[str] = frozenset(),
) -> str | None:
    """List of objects where each carries its own list of LINES.

    ``_check_items`` cannot express this: its values are scalars, and
    three of the redesign's bands need one level of nesting — a card
    with bullet points, a step with a list of protocols. Bounded at
    both levels, because this is admin-authored JSON.
    """
    if not isinstance(value, list) or len(value) > max_items:
        return f"{name}: must be a list of at most {max_items} entries"
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"{name}[{i}]: must be an object"
        item = {str(k): v for k, v in raw.items()}
        for key, max_length in required.items():
            entry = item.get(key)
            if not entry or not _is_str(entry, max_length):
                return f"{name}[{i}].{key}: required string (max {max_length})"
        for key, max_length in optional.items():
            entry = item.get(key)
            if entry is not None and not _is_str(entry, max_length):
                return f"{name}[{i}].{key}: must be a string (max {max_length})"
        for key in icon_keys:
            entry = item.get(key)
            if entry is not None and not _ICON_RE.match(str(entry)):
                return f"{name}[{i}].{key}: must be an i-* icon name"
        lines = item.get(lines_key, [])
        if not isinstance(lines, list) or len(lines) > max_lines:
            return (
                f"{name}[{i}].{lines_key}: must be a list of at most "
                f"{max_lines} entries"
            )
        for j, line in enumerate(lines):
            if not line or not _is_str(line, line_length):
                return (
                    f"{name}[{i}].{lines_key}[{j}]: required string "
                    f"(max {line_length})"
                )
        unknown = set(item) - set(required) - set(optional) - {lines_key}
        if unknown:
            return f"{name}[{i}]: unknown keys {sorted(unknown)}"
    return None


_OPTION_TEXT = (
    ("label", 40),
    ("model", 120),
    ("title", 120),
    ("rationale", 600),
    ("note", 400),
    ("cta_text", 100),
)


def _check_options(value) -> str | None:
    """The options an ``option_selector`` switches between."""
    if not isinstance(value, list) or len(value) > 4:
        return "options: must be a list of at most 4 entries"
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"options[{i}]: must be an object"
        option = {str(k): v for k, v in raw.items()}
        if not option.get("name") or not _is_str(option.get("name"), 60):
            return f"options[{i}].name: required string (max 60)"
        for key, max_length in _OPTION_TEXT:
            entry = option.get(key)
            if entry is not None and not _is_str(entry, max_length):
                return (
                    f"options[{i}].{key}: must be a string (max {max_length})"
                )
        link = option.get("cta_link")
        if link is not None and not (
            _is_str(link, 1000) and _LINK_RE.match(link)
        ):
            return f"options[{i}].cta_link: internal path or https URL"
        rows = option.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 12:
            return f"options[{i}].rows: must be a list of at most 12 rows"
        for j, row_raw in enumerate(rows):
            if not isinstance(row_raw, dict):
                return f"options[{i}].rows[{j}]: must be an object"
            row = {str(k): v for k, v in row_raw.items()}
            for key, max_length in (("label", 60), ("value", 120)):
                if not row.get(key) or not _is_str(row.get(key), max_length):
                    return (
                        f"options[{i}].rows[{j}].{key}: required string "
                        f"(max {max_length})"
                    )
            unknown = set(row) - {"label", "value"}
            if unknown:
                return f"options[{i}].rows[{j}]: unknown keys {sorted(unknown)}"
        unknown = (
            set(option)
            - {"name", "cta_link", "rows"}
            - {key for key, _ in _OPTION_TEXT}
        )
        if unknown:
            return f"options[{i}]: unknown keys {sorted(unknown)}"
    return None


def _check_matrix_rows(value) -> str | None:
    """``label`` plus one value PER COLUMN of a comparison table.

    A ragged row is refused rather than padded: a table whose rows
    disagree about how many columns there are prints a value under the
    wrong heading, which is worse than not printing it.
    """
    if not isinstance(value, list) or len(value) > 24:
        return "rows: must be a list of at most 24 entries"
    width = None
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"rows[{i}]: must be an object"
        row = {str(k): v for k, v in raw.items()}
        if not row.get("label") or not _is_str(row.get("label"), 60):
            return f"rows[{i}].label: required string (max 60)"
        values = row.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            return f"rows[{i}].values: must be a list of 1-4 values"
        if width is None:
            width = len(values)
        elif len(values) != width:
            return (
                f"rows[{i}].values: {len(values)} values where the first "
                f"row has {width} - every row spans the same columns"
            )
        for j, cell in enumerate(values):
            if not _is_str(cell, 120):
                return f"rows[{i}].values[{j}]: must be a string (max 120)"
        unknown = set(row) - {"label", "values"}
        if unknown:
            return f"rows[{i}]: unknown keys {sorted(unknown)}"
    return None


def _check_callout(value) -> str | None:
    """A boxed aside beside a page hero.

    ``tone`` is an enum, not a colour: the storefront decides what
    "warning" looks like from its own tokens, so a merchant cannot
    author a hex into a page.
    """
    if not isinstance(value, dict):
        return "callout: must be an object"
    callout = {str(k): v for k, v in value.items()}
    if callout.get("tone") not in (None, "info", "warning", "success"):
        return "callout.tone: one of info/warning/success"
    if not callout.get("title") or not _is_str(callout.get("title"), 100):
        return "callout.title: required string (max 100)"
    for key, max_length in (("text", 400), ("note", 160)):
        entry = callout.get(key)
        if entry is not None and not _is_str(entry, max_length):
            return f"callout.{key}: must be a string (max {max_length})"
    unknown = set(callout) - {"tone", "title", "text", "note"}
    if unknown:
        return f"callout: unknown keys {sorted(unknown)}"
    return None


def _check_prompt(value) -> str | None:
    """The "did not find yours?" card a grid can end with.

    A grid of what a merchant offers invites the question of what it
    does not, and the artboards answer it in the grid's last cell
    rather than under it — so this is one cell's worth of copy, not a
    section.
    """
    if not isinstance(value, dict):
        return "prompt: must be an object"
    prompt = {str(k): v for k, v in value.items()}
    if not prompt.get("title") or not _is_str(prompt.get("title"), 100):
        return "prompt.title: required string (max 100)"
    for key, max_length in (("text", 300), ("cta_text", 100)):
        entry = prompt.get(key)
        if entry is not None and not _is_str(entry, max_length):
            return f"prompt.{key}: must be a string (max {max_length})"
    link = prompt.get("cta_link")
    if link is not None and not (_is_str(link, 1000) and _LINK_RE.match(link)):
        return "prompt.cta_link: internal path or https URL"
    unknown = set(prompt) - {"title", "text", "cta_text", "cta_link"}
    if unknown:
        return f"prompt: unknown keys {sorted(unknown)}"
    return None


def _check_spec_cards(value) -> str | None:
    """The comparison cards a ``media_text`` band shows beside its copy.

    One card per option (``name`` plus an optional model line), each
    holding a short table of label/value rows — the shape a merchant
    uses to put two or three product tiers side by side. Bounded twice
    over, cards and rows, because this is admin-authored JSON.
    """
    if not isinstance(value, list) or len(value) > 4:
        return "specs: must be a list of at most 4 cards"
    for i, raw in enumerate(value):
        if not isinstance(raw, dict):
            return f"specs[{i}]: must be an object"
        card = {str(k): v for k, v in raw.items()}
        if not _is_str(card.get("name", ""), 60) or not card.get("name"):
            return f"specs[{i}].name: required string (max 60)"
        for key, max_length in (("label", 40), ("subtitle", 120)):
            entry = card.get(key)
            if entry is not None and not _is_str(entry, max_length):
                return f"specs[{i}].{key}: must be a string (max {max_length})"
        rows = card.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 8:
            return f"specs[{i}].rows: must be a list of at most 8 rows"
        for j, row_raw in enumerate(rows):
            if not isinstance(row_raw, dict):
                return f"specs[{i}].rows[{j}]: must be an object"
            row = {str(k): v for k, v in row_raw.items()}
            for key, max_length in (("label", 40), ("value", 80)):
                if not row.get(key) or not _is_str(row.get(key), max_length):
                    return (
                        f"specs[{i}].rows[{j}].{key}: "
                        f"required string (max {max_length})"
                    )
            unknown = set(row) - {"label", "value"}
            if unknown:
                return f"specs[{i}].rows[{j}]: unknown keys {sorted(unknown)}"
        unknown = set(card) - {"label", "name", "subtitle", "rows"}
        if unknown:
            return f"specs[{i}]: unknown keys {sorted(unknown)}"
    return None


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
        # The proof row under the copy — "50+ documented projects",
        # "7 fields", "2 offices". Merchant facts that change as the
        # business grows, so props rather than markup.
        "stats": lambda v: _check_items(
            v,
            max_items=4,
            required={"value": 12, "label": 80},
            optional={},
            name="stats",
        ),
        "subheading": lambda v: None if _is_str(v, 500) else "string ≤500",
        "eyebrow": lambda v: None if _is_str(v, 100) else "string ≤100",
        "image_url": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "secondary_cta_text": lambda v: (
            None if _is_str(v, 100) else "string ≤100"
        ),
        "secondary_cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "overlay_opacity": lambda v: (
            None
            if isinstance(v, (int, float)) and 0 <= v <= 1
            else "number between 0 and 1"
        ),
        "decor": lambda v: (
            None
            if v in ("none", "orbs", "gradient")
            else "one of none/orbs/gradient"
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
        # Mobile/tablet variants (matching indices); Nuxt falls back to
        # ``images`` when absent. Serialized to camelCase mobileImages.
        "mobile_images": lambda v: (
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
    "divider": {
        # ``thread`` renders the woven tri-strand divider (derived from
        # the tenant's primary/secondary/accent tokens).
        "variant": lambda v: (
            None if v in ("line", "thread") else "one of line/thread"
        ),
    },
    "loyalty_hero": {},
    "search_bar": {},
    "about_content": {},
    "vision_content": {},
    "what_is_microlearning": {},
    "why_microlearning": {},
    # Weekly schedule + open/closed badge; data comes from the
    # BUSINESS_HOURS extra_setting, so the section carries no props.
    "business_hours": {},
    "location_map": {
        "embed_url": lambda v: (
            None
            if _is_str(v, 1000) and str(v).startswith("https://")
            else "https URL ≤1000"
        ),
        "lat": lambda v: (
            None
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and -90 <= v <= 90
            else "number between -90 and 90"
        ),
        "lng": lambda v: (
            None
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and -180 <= v <= 180
            else "number between -180 and 180"
        ),
        "address": lambda v: None if _is_str(v, 300) else "string ≤300",
    },
    "partner_strip": {
        "label": lambda v: None if _is_str(v, 80) else "string ≤80",
        "items": lambda v: _check_items(
            v,
            max_items=12,
            required={"name": 60},
            optional={"href": 1000},
            link_keys=frozenset({"href"}),
        ),
    },
    "page_hero": {
        # The top of an inner page: what every one of them opens with.
        # A band, not a page template — the same shape serves a product
        # page's hero, a register's, and a contact page's.
        "eyebrow": lambda v: None if _is_str(v, 100) else "string ≤100",
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "standfirst": lambda v: None if _is_str(v, 300) else "string ≤300",
        "body": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "secondary_cta_text": lambda v: (
            None if _is_str(v, 100) else "string ≤100"
        ),
        "secondary_cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "stats": lambda v: _check_items(
            v,
            max_items=4,
            required={"value": 12, "label": 80},
            optional={},
            name="stats",
        ),
        "callout": _check_callout,
        "facts": lambda v: _check_items(
            v,
            max_items=6,
            required={"label": 40, "value": 60},
            optional={},
            name="facts",
        ),
    },
    "feature_lists": {
        # Two or three cards, each an icon, a title and a checklist —
        # what a product page uses to say what a thing is and what it
        # does, with one shared footnote under them.
        "heading": lambda v: None if _is_str(v, 200) else "string \u2264200",
        "note": lambda v: None if _is_str(v, 1000) else "string \u22641000",
        "emphasis": lambda v: None if _is_str(v, 120) else "string \u2264120",
        "items": lambda v: _check_nested_lines(
            v,
            name="items",
            max_items=4,
            required={"title": 100},
            optional={"icon": 100},
            lines_key="bullets",
            max_lines=8,
            line_length=300,
            icon_keys=frozenset({"icon"}),
        ),
    },
    "option_selector": {
        # Pick one of a few options and see it in detail — the shape a
        # product FAMILY needs when the choice between its members is
        # the whole point of the page.
        "heading": lambda v: None if _is_str(v, 200) else "string \u2264200",
        "standfirst": lambda v: None if _is_str(v, 400) else "string \u2264400",
        "rows_label": lambda v: None if _is_str(v, 60) else "string \u226460",
        "rationale_label": lambda v: (
            None if _is_str(v, 60) else "string \u226460"
        ),
        "options": _check_options,
    },
    "comparison_table": {
        "heading": lambda v: None if _is_str(v, 200) else "string \u2264200",
        "row_label": lambda v: None if _is_str(v, 60) else "string \u226460",
        "note": lambda v: None if _is_str(v, 600) else "string \u2264600",
        "columns": lambda v: (
            None
            if isinstance(v, list)
            and 1 <= len(v) <= 4
            and all(c and _is_str(c, 60) for c in v)
            else "columns: 1-4 non-empty strings \u226460"
        ),
        "rows": _check_matrix_rows,
    },
    "flow_steps": {
        "heading": lambda v: None if _is_str(v, 200) else "string \u2264200",
        "body": lambda v: None if _is_str(v, 600) else "string \u2264600",
        "items": lambda v: _check_nested_lines(
            v,
            name="items",
            max_items=4,
            required={"title": 100},
            optional={"label": 60},
            lines_key="lines",
            max_lines=8,
            line_length=120,
        ),
    },
    "reference_cards": {
        # A curated few of something a longer page lists in full: each
        # card is a label, a title, a line about it, and one
        # attribution whose LABEL is shared by the band ("On behalf
        # of", "Client", "Year") and whose value is per card.
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "meta_label": lambda v: None if _is_str(v, 40) else "string ≤40",
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "items": lambda v: _check_items(
            v,
            max_items=6,
            required={"title": 160},
            optional={"label": 60, "text": 300, "meta": 80},
        ),
    },
    "project_register": {
        # A register: one numbered row per installation, filterable by
        # sector. ``items[].sector`` is a KEY into ``sectors`` — see
        # ``_cross_check_project_register``, which is what makes the
        # pill colour resolvable at render time.
        #
        # 200 rows because a reference list GROWS (48 today) and the
        # storefront prints all of them at once — there is no
        # pagination in the design and a register that silently stops
        # at the fiftieth project would be worse than a long page.
        "meta_label": lambda v: None if _is_str(v, 60) else "string \u226460",
        "note": lambda v: None if _is_str(v, 400) else "string \u2264400",
        "sectors": lambda v: _check_items(
            v,
            max_items=12,
            required={"key": 40, "label": 40},
            optional={},
            name="sectors",
        ),
        "items": lambda v: _check_items(
            v,
            max_items=200,
            required={"title": 200},
            optional={"sector": 40, "note": 200, "meta": 120},
        ),
    },
    "pull_quote": {
        # A stated principle with the reason under it — not a
        # testimonial, which is somebody else's words and needs an
        # attribution to mean anything.
        "quote": lambda v: None if _is_str(v, 300) else "string ≤300",
        "text": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "attribution": lambda v: None if _is_str(v, 120) else "string ≤120",
    },
    "features_grid": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "items": lambda v: _check_items(
            v,
            max_items=12,
            required={"title": 100},
            optional={"text": 500, "icon": 100},
            icon_keys=frozenset({"icon"}),
        ),
        "columns": lambda v: None if _is_int(v, 1, 4) else "int 1–4",
        "decor": lambda v: (
            None
            if v in ("none", "gradient_tiles")
            else "one of none/gradient_tiles"
        ),
        # The band's own link, beside the heading rather than under the
        # grid — the artboards put "all of them →" there on every band
        # whose grid is a subset of a longer page.
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "prompt": _check_prompt,
    },
    "media_text": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "body": lambda v: None if _is_str(v, 5000) else "string ≤5000",
        # A label above the heading, a footnote under the body, a
        # checklist, and comparison cards for the side of the band that
        # carries no image — the four things a "text plus something
        # beside it" band needs and had no shape for.
        "eyebrow": lambda v: None if _is_str(v, 100) else "string ≤100",
        "note": lambda v: None if _is_str(v, 200) else "string ≤200",
        # One phrase inside ``body`` to set in the emphasis weight. A
        # SUBSTRING, not markup: ``body`` renders as text, and an HTML
        # prop would be an injection surface for one bold phrase.
        "emphasis": lambda v: None if _is_str(v, 120) else "string ≤120",
        "bullets": lambda v: _check_items(
            v,
            max_items=6,
            required={"text": 300},
            optional={},
            name="bullets",
        ),
        "specs": _check_spec_cards,
        "image_url": lambda v: None if _is_str(v, 1000) else "string ≤1000",
        "image_position": lambda v: (
            None if v in ("left", "right") else "one of left/right"
        ),
        "cta_text": lambda v: None if _is_str(v, 100) else "string ≤100",
        "cta_link": lambda v: (
            None
            if _is_str(v, 1000) and _LINK_RE.match(v)
            else "internal path or https URL"
        ),
        "decor": lambda v: (
            None
            if v in ("none", "orbs", "gradient")
            else "one of none/orbs/gradient"
        ),
    },
    "image_gallery": {
        "items": lambda v: _check_items(
            v,
            max_items=24,
            required={"src": 1000, "alt": 200},
            optional={"caption": 200},
        ),
        "columns": lambda v: None if _is_int(v, 2, 4) else "int 2–4",
    },
    "story_timeline": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "subheading": lambda v: None if _is_str(v, 500) else "string ≤500",
        "items": lambda v: _check_items(
            v,
            max_items=20,
            required={"title": 100},
            optional={"date": 50, "text": 500, "icon": 100},
            icon_keys=frozenset({"icon"}),
        ),
    },
    "faq": {
        "heading": lambda v: None if _is_str(v, 200) else "string ≤200",
        "items": lambda v: _check_items(
            v,
            max_items=30,
            required={"question": 200, "answer": 2000},
            optional={},
        ),
        "multiple": lambda v: None if isinstance(v, bool) else "boolean",
    },
}


def _cross_check_project_register(props: dict) -> list[str]:
    """Every row's sector must be one of the declared sectors.

    The only check in this module that spans two props, because it is
    the only place one prop is a KEY into another: the storefront
    resolves a row's pill colour by the sector's POSITION in
    ``sectors``, so a key that isn't there loses its colour silently.
    Cheap to get wrong by hand in the admin, invisible afterwards.
    """
    declared = {
        sector.get("key")
        for sector in props.get("sectors") or []
        if isinstance(sector, dict)
    }
    if not declared:
        return []
    unknown = sorted(
        {
            str(item.get("sector"))
            for item in props.get("items") or []
            if isinstance(item, dict)
            # ``sector`` is optional: an unclassified row is a plain
            # row, and only a stated sector can be a wrong one.
            and item.get("sector")
            and item.get("sector") not in declared
        }
    )
    if not unknown:
        return []
    return [f"items: sector(s) not declared in sectors: {unknown}"]


# Whole-props checks, run only once every per-key check has passed:
# a cross-prop rule cannot say anything useful about a prop whose
# SHAPE is already wrong.
_CROSS_VALIDATORS: dict[str, Callable[[dict], list[str]]] = {
    "project_register": _cross_check_project_register,
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

    cross = _CROSS_VALIDATORS.get(component_type)
    if not errors and cross is not None:
        errors.extend(cross(props))

    if errors:
        raise ValidationError(
            f"Invalid props for {component_type}: " + "; ".join(errors)
        )


_ICON_RE = re.compile(r"^i-[a-z0-9:-]+$")


def _check_nav_link(prefix: str, item: object) -> list[str]:
    if not isinstance(item, dict):
        return [f"{prefix}: must be an object"]
    errors: list[str] = []
    entry = {str(k): v for k, v in item.items()}
    label = entry.get("label")
    if not _is_str(label or "", 100) or not label:
        errors.append(f"{prefix}.label: required string ≤100")
    to = entry.get("to")
    href = entry.get("href")
    if bool(to) == bool(href):
        errors.append(f"{prefix}: exactly one of 'to' or 'href' required")
    if to is not None and (
        not _is_str(to, 1000) or not str(to).startswith("/")
    ):
        errors.append(f"{prefix}.to: must be an internal path")
    if href is not None and (
        not _is_str(href, 1000) or not str(href).startswith("https://")
    ):
        errors.append(f"{prefix}.href: must be an https URL")
    icon = entry.get("icon")
    if icon is not None and (
        not isinstance(icon, str) or not _ICON_RE.match(icon)
    ):
        errors.append(f"{prefix}.icon: must be an i-* icon name")
    unknown = set(entry) - {"label", "to", "href", "icon"}
    if unknown:
        errors.append(f"{prefix}: unknown keys {sorted(unknown)}")
    return errors


def validate_navigation_items(slot: str, items: object) -> None:
    """Raise ``ValidationError`` when a NavigationMenu payload doesn't
    fit its slot's shape."""
    if items in (None, []):
        return
    if not isinstance(items, list) or len(items) > 50:
        raise ValidationError("items must be a list of at most 50 entries.")

    errors: list[str] = []
    if slot == "footer":
        for i, raw in enumerate(items):
            if not isinstance(raw, dict):
                errors.append(f"[{i}]: must be an object")
                continue
            column = {str(k): v for k, v in raw.items()}
            label = column.get("label")
            if not _is_str(label or "", 100) or not label:
                errors.append(f"[{i}].label: required string ≤100")
            icon = column.get("icon")
            if icon is not None and (
                not isinstance(icon, str) or not _ICON_RE.match(icon)
            ):
                errors.append(f"[{i}].icon: must be an i-* icon name")
            children = column.get("children")
            if not isinstance(children, list) or not children:
                errors.append(f"[{i}].children: required non-empty list")
            else:
                for j, child in enumerate(children):
                    errors.extend(
                        _check_nav_link(f"[{i}].children[{j}]", child)
                    )
            unknown = set(column) - {"label", "icon", "children"}
            if unknown:
                errors.append(f"[{i}]: unknown keys {sorted(unknown)}")
    else:
        for i, item in enumerate(items):
            errors.extend(_check_nav_link(f"[{i}]", item))

    if errors:
        raise ValidationError(
            f"Invalid {slot} navigation: " + "; ".join(errors)
        )


def _check_locale_keys(i18n: object, label: str) -> dict:
    """Shared shape check for the two per-locale override fields.

    The default locale is NOT a valid key: its values are the columns
    above the override (``props``/``items``), so accepting it here would
    give one locale two sources of truth and no rule for which wins.
    """
    if not isinstance(i18n, dict):
        raise ValidationError(f"{label} must be a JSON object.")

    codes = available_language_codes()
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE
    errors: list[str] = []
    for code in i18n:
        if not isinstance(code, str) or code not in codes:
            errors.append(f"{code!r}: not a language this store serves")
        elif code == default:
            errors.append(
                f"{code!r}: is the default locale — edit the fields "
                "themselves rather than overriding them"
            )
    if errors:
        raise ValidationError(f"Invalid {label}: " + "; ".join(errors))
    return {str(k): v for k, v in i18n.items()}


def validate_section_i18n(component_type: str, i18n: object) -> None:
    """Raise ``ValidationError`` when a section's per-locale overrides
    don't fit ``{"<locale>": {"title": str, "props": {...}}}``.

    ``props`` here is a PARTIAL override merged over the section's own
    props at render time, so every key is optional and each is checked
    against the same per-component contract
    (``validate_section_props``) — a locale cannot introduce a prop the
    component does not have.
    """
    if i18n in (None, {}):
        return

    for code, override in _check_locale_keys(i18n, "i18n").items():
        if not isinstance(override, dict):
            raise ValidationError(f"i18n.{code}: must be a JSON object.")
        unknown = set(override) - {"title", "props"}
        if unknown:
            raise ValidationError(
                f"i18n.{code}: unknown keys {sorted(unknown)} "
                "(only 'title' and 'props')"
            )
        title = override.get("title")
        if title is not None and not _is_str(title, 200):
            raise ValidationError(f"i18n.{code}.title: string ≤200 required")
        if "props" in override:
            try:
                validate_section_props(component_type, override["props"])
            except ValidationError as exc:
                raise ValidationError(
                    f"i18n.{code}.props: " + "; ".join(exc.messages)
                ) from exc


def validate_navigation_i18n(slot: str, i18n: object) -> None:
    """Raise ``ValidationError`` when a menu's per-locale overrides
    don't fit ``{"<locale>": [...items...]}``.

    A menu is translated WHOLE rather than per-item: the items are a
    list, so an index-keyed overlay would silently retarget every label
    the first time an operator reorders the menu.
    """
    if i18n in (None, {}):
        return

    for code, items in _check_locale_keys(i18n, "i18n").items():
        try:
            validate_navigation_items(slot, items)
        except ValidationError as exc:
            raise ValidationError(
                f"i18n.{code}: " + "; ".join(exc.messages)
            ) from exc
