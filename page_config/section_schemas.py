"""JSON Schema for every page section's ``props`` and ``i18n``.

The canonical contract of ``PageSection``: Django validates writes
against it (``page_config.schemas``), the admin edits props through it,
and the API publishes it (camelized) for the storefront to generate its
render-time parser from. Keys are snake_case, as stored; the API
camelizes on the wire.

Top-level props are all optional, since a section renders its defaults
for any it lacks. List entries name the keys they require. Every object
is closed (``additionalProperties: false``): a typo is refused at the
admin instead of rendering a default. Two rules span props and stay in
Python (``page_config.schemas``): a register's rows name declared
sectors, and a comparison table's rows are equally wide.

Self-contained, no ``$ref``: the editor Unfold ships has no reference
resolver.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from core.utils.i18n import available_language_codes

Schema = dict[str, Any]

# ── Scalars ──────────────────────────────────────────────────────────


def text(max_length: int) -> Schema:
    return {"type": "string", "maxLength": max_length}


def required_text(max_length: int) -> Schema:
    """Present and non-empty."""
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def integer(minimum: int, maximum: int) -> Schema:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def number(minimum: float, maximum: float) -> Schema:
    return {"type": "number", "minimum": minimum, "maximum": maximum}


def choice(*values: str) -> Schema:
    return {"enum": list(values)}


BOOLEAN: Schema = {"type": "boolean"}
# An internal path or an https URL.
LINK: Schema = {"type": "string", "maxLength": 1000, "pattern": "^(/|https://)"}
HTTPS_URL: Schema = {
    "type": "string",
    "maxLength": 1000,
    "pattern": "^https://",
}
ICON: Schema = {"type": "string", "pattern": "^i-[a-z0-9:-]+$"}
HEX: Schema = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
IMAGE_URL = text(1000)
ID = integer(1, 2_147_483_647)
# WHICH of the two page surfaces a band paints. A page built from
# full-width bands separates two of them by alternating ground and
# raised, so the choice belongs to the PAGE — the same band sits last on
# one layout and after a raised one on another. Every section that draws
# a band carries it; a hero paints its own ground and does not.
SURFACE = choice("default", "muted")


def icon(max_length: int) -> Schema:
    return {**ICON, "maxLength": max_length}


# ── Objects and lists ────────────────────────────────────────────────


def obj(
    properties: dict[str, Schema], required: tuple[str, ...] = ()
) -> Schema:
    schema: Schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def array(item: Schema, max_items: int, min_items: int = 0) -> Schema:
    schema: Schema = {"type": "array", "items": item, "maxItems": max_items}
    if min_items:
        schema["minItems"] = min_items
    return schema


def entries(
    max_items: int,
    required: dict[str, int],
    optional: dict[str, Schema] | None = None,
) -> Schema:
    """A list of objects: ``required`` maps each required key to its
    maximum length (present, non-empty); ``optional`` maps the rest to
    their schemas."""
    properties = {key: required_text(n) for key, n in required.items()}
    properties.update(optional or {})
    return array(obj(properties, tuple(required)), max_items)


def lines(max_items: int, max_length: int) -> Schema:
    return array(required_text(max_length), max_items)


# ── Shared shapes ────────────────────────────────────────────────────

STATS = entries(4, {"value": 12, "label": 80})

# The "did not find yours?" card a grid can end with: one cell's worth
# of copy, not a section.
PROMPT = obj(
    {
        "title": required_text(100),
        "text": text(300),
        "cta_text": text(100),
        "cta_link": LINK,
    },
    required=("title",),
)

# A boxed aside beside a page hero. ``tone`` is an enum, not a colour:
# the storefront decides what "warning" looks like from its own tokens.
CALLOUT = obj(
    {
        "tone": choice("info", "warning", "success"),
        "title": required_text(100),
        "text": text(400),
        "note": text(160),
    },
    required=("title",),
)

# A carousel of full editorial slides. Each owns its copy and its
# destination; when ``slides`` is present the flat props are ignored. No
# ``theme``: the copy sits on the accent panel beside the artwork.
SLIDES = array(
    obj(
        {
            "image_url": required_text(1000),
            "mobile_image_url": text(1000),
            "alt": text(200),
            "eyebrow": text(100),
            "heading": text(200),
            "subheading": text(500),
            "cta_text": text(100),
            "cta_link": LINK,
            "secondary_cta_text": text(100),
            "secondary_cta_link": LINK,
        },
        required=("image_url",),
    ),
    8,
)

# A product tier's comparison card: a name, a model line, and a short
# table of label/value rows.
SPEC_CARDS = array(
    obj(
        {
            "label": text(40),
            "name": required_text(60),
            "subtitle": text(120),
            "rows": entries(8, {"label": 40, "value": 80}),
        },
        required=("name",),
    ),
    4,
)

TESTIMONIALS = array(
    obj(
        {
            "name": text(100),
            "text": text(1000),
            "avatar": text(1000),
            # Who the quote is FROM — "Verified buyer", "Χονδρική".
            "role": text(100),
            # Displayed as stars: the FIVE-point scale a reader expects,
            # not ProductReview's internal 1..10.
            "rating": integer(1, 5),
        }
    ),
    20,
)

# ``kind`` decides where a badge comes from and whether it may render:
# ``ai`` shows only where the tenant's agent-commerce flag is on. A badge
# with neither a logo nor an icon would be a bare word in a row of marks.
BADGES = array(
    {
        **obj(
            {
                "kind": choice("payment", "shipping", "ai", "custom"),
                "label": required_text(60),
                "image_url": IMAGE_URL,
                "icon": ICON,
                "href": LINK,
            },
            required=("kind", "label"),
        ),
        "anyOf": [{"required": ["image_url"]}, {"required": ["icon"]}],
    },
    12,
)

# The options an ``option_selector`` switches between: eight, because a
# page whose SUBJECT is a list puts every one in the rail.
OPTIONS = array(
    obj(
        {
            "name": required_text(60),
            "label": text(40),
            "model": text(120),
            "title": text(120),
            "rationale": text(600),
            "note": text(400),
            "cta_text": text(100),
            "cta_link": LINK,
            "bullets": lines(10, 200),
            "rows": entries(12, {"label": 60, "value": 120}),
        },
        required=("name",),
    ),
    8,
)

# ``label`` plus one value per column; equal widths are checked in
# Python (a ragged row prints a value under the wrong heading).
MATRIX_ROWS = array(
    obj(
        {
            "label": required_text(60),
            "values": array(text(120), 4, min_items=1),
        },
        required=("label", "values"),
    ),
    24,
)


def nested_lines(
    max_items: int,
    required: dict[str, int],
    optional: dict[str, Schema],
    lines_key: str,
    max_lines: int,
    line_length: int,
) -> Schema:
    """Entries that each carry their own list of lines — a card with
    bullet points, a step with its protocols."""
    return entries(
        max_items,
        required,
        {**optional, lines_key: lines(max_lines, line_length)},
    )


def product_rail(max_page_size: int) -> dict[str, Schema]:
    """A rail of products — the same band three times over. ``ordering``
    is what makes two rails on one page different bands."""
    return {
        "surface": SURFACE,
        "heading": text(200),
        "subheading": text(500),
        "cta_text": text(100),
        "cta_link": LINK,
        "ordering": choice(
            "featured", "newest", "popular", "discounted", "rating"
        ),
        # Narrow the rail to one category. The id, not a slug: a slug is
        # translatable and a rename would silently empty the band.
        "category_id": ID,
        "show_add_to_cart": BOOLEAN,
        "page_size": integer(1, max_page_size),
    }


BLOG_RAIL: dict[str, Schema] = {
    "surface": SURFACE,
    "heading": text(200),
    "subheading": text(500),
    "cta_text": text(100),
    "cta_link": LINK,
    "category_id": ID,
}

# ── Every section ────────────────────────────────────────────────────

SECTION_PROPS: dict[str, dict[str, Schema]] = {
    "hero_banner": {
        "heading": text(200),
        # The proof row under the copy — merchant facts that change as
        # the business grows.
        "stats": STATS,
        "subheading": text(500),
        "eyebrow": text(100),
        "image_url": IMAGE_URL,
        "cta_text": text(100),
        "cta_link": LINK,
        "secondary_cta_text": text(100),
        "secondary_cta_link": LINK,
        "overlay_opacity": number(0, 1),
        "decor": choice("none", "orbs", "gradient"),
        # A hero crops to a different shape on a phone than on a desk.
        "mobile_image_url": IMAGE_URL,
        "image_alt": text(200),
        "align": choice("left", "center"),
        # Which way the copy reads over the artwork; ``auto`` keeps the
        # component's own contrast choice.
        "theme": choice("light", "dark", "auto"),
    },
    "hero_carousel": {
        "slides": SLIDES,
        # 0 = no autoplay. Under three seconds is unreadable, and the
        # component pauses under prefers-reduced-motion regardless.
        "autoplay_ms": {"oneOf": [{"const": 0}, integer(3000, 15000)]},
        "aspect": choice("wide", "banner", "square"),
        "images": array(text(1000), 10),
        # Mobile variants at matching indices; the storefront falls back
        # to ``images``.
        "mobile_images": array(text(1000), 10),
        "link": LINK,
    },
    "products_slider": product_rail(24),
    "products_grid": product_rail(48),
    "featured_products": {**product_rail(24), "columns": integer(1, 6)},
    "product_categories": {
        "surface": SURFACE,
        "heading": text(200),
        # A swipeable rail, a plain grid or image tiles: presentation the
        # page owns.
        "layout": choice("slider", "grid", "tiles"),
        # Draw the CHILDREN of one category instead of the roots.
        "parent_id": ID,
        "limit": integer(1, 24),
    },
    "blog_categories": {},
    "blog_posts_carousel": {**BLOG_RAIL, "count": integer(1, 12)},
    "blog_posts_grid": {**BLOG_RAIL, "count": integer(1, 24)},
    "blog_posts_list": {**BLOG_RAIL, "page_size": integer(1, 24)},
    "recently_viewed": {"heading": text(200)},
    "rich_text": {"content": text(20000)},
    "cta_banner": {
        "heading": text(200),
        "description": text(1000),
        "button_text": text(100),
        "button_link": LINK,
        "background_color": HEX,
        # A surface, not a colour: a CTA lands last on one page and
        # after a raised band on another.
        "surface": SURFACE,
    },
    "newsletter_signup": {
        "heading": text(200),
        "description": text(1000),
        "placeholder": text(100),
        "button_text": text(60),
        "surface": SURFACE,
    },
    "testimonials": {
        "surface": SURFACE,
        "heading": text(200),
        "items": TESTIMONIALS,
    },
    "spacer": {"height": choice("sm", "md", "lg", "xl")},
    # ``thread`` renders the woven tri-strand divider from the tenant's
    # primary/secondary/accent tokens.
    "divider": {"variant": choice("line", "thread")},
    "loyalty_hero": {},
    "search_bar": {},
    "about_content": {},
    "vision_content": {},
    "what_is_microlearning": {},
    "why_microlearning": {},
    # Data comes from the BUSINESS_HOURS setting.
    "business_hours": {"surface": SURFACE},
    "location_map": {
        "surface": SURFACE,
        "embed_url": HTTPS_URL,
        "lat": number(-90, 90),
        "lng": number(-180, 180),
        "address": text(300),
    },
    "partner_strip": {
        "label": text(80),
        "items": entries(12, {"name": 60}, {"href": LINK}),
    },
    # The top of an inner page: the same band opens a product page, a
    # register and a contact page.
    "page_hero": {
        "eyebrow": text(100),
        "heading": text(200),
        "standfirst": text(300),
        "body": text(1000),
        "cta_text": text(100),
        "cta_link": LINK,
        "secondary_cta_text": text(100),
        "secondary_cta_link": LINK,
        "stats": STATS,
        "callout": CALLOUT,
        "facts": entries(6, {"label": 40, "value": 60}),
    },
    # Two or three cards, each an icon, a title and a checklist, with
    # one shared footnote under them.
    "feature_lists": {
        "heading": text(200),
        "note": text(1000),
        "emphasis": text(120),
        "items": nested_lines(
            4, {"title": 100}, {"icon": icon(100)}, "bullets", 8, 300
        ),
    },
    # Pick one of a few options and see it in detail.
    "option_selector": {
        "heading": text(200),
        "standfirst": text(400),
        "rows_label": text(60),
        "rationale_label": text(60),
        "bullets_label": text(60),
        # cards: variants of one product as boxed tabs; strip: a
        # numbered SEQUENCE as underlined tabs; rail: a list that IS the
        # page's subject, beside the panel.
        "layout": choice("cards", "strip", "rail"),
        "prompt": PROMPT,
        "options": OPTIONS,
    },
    "comparison_table": {
        "heading": text(200),
        "row_label": text(60),
        "note": text(600),
        "columns": array(required_text(60), 4, min_items=1),
        "rows": MATRIX_ROWS,
    },
    "flow_steps": {
        "heading": text(200),
        "body": text(600),
        "items": nested_lines(
            4, {"title": 100}, {"label": text(60)}, "lines", 8, 120
        ),
    },
    # A curated few of something a longer page lists in full; the
    # attribution LABEL is shared by the band, its value is per card.
    "reference_cards": {
        "heading": text(200),
        "meta_label": text(40),
        "cta_text": text(100),
        "cta_link": LINK,
        "items": entries(
            6,
            {"title": 160},
            {"label": text(60), "text": text(300), "meta": text(80)},
        ),
    },
    # One numbered row per installation, filterable by sector. 200 rows
    # because a register GROWS and the storefront prints all of them.
    "project_register": {
        "meta_label": text(60),
        "note": text(400),
        "sectors": entries(12, {"key": 40, "label": 40}),
        "items": entries(
            200,
            {"title": 200},
            {"sector": text(40), "note": text(200), "meta": text(120)},
        ),
    },
    # One card per manufacturer or platform; the tags are CHIPS — a part
    # number, a bus, a band.
    "vendor_cards": {
        "note": text(400),
        "items": nested_lines(
            8,
            {"title": 60},
            {"label": text(60), "text": text(600)},
            "tags",
            8,
            40,
        ),
    },
    # The contact page as one band. The offices come from the
    # STORE_OFFICES setting and the form labels are UI, so neither is here.
    "contact_panel": {
        "eyebrow": text(100),
        "heading": text(200),
        "body": text(600),
        "hint": text(400),
        "response_time": text(120),
        "subjects": entries(6, {"label": 40}),
    },
    # A stated principle with the reason under it — not a testimonial.
    "pull_quote": {
        "quote": text(300),
        "text": text(1000),
        "attribution": text(120),
    },
    "features_grid": {
        "surface": SURFACE,
        "heading": text(200),
        "items": entries(
            12, {"title": 100}, {"text": text(500), "icon": icon(100)}
        ),
        "body": text(600),
        "columns": integer(1, 4),
        "decor": choice("none", "gradient_tiles", "framed"),
        # The band's own link, beside the heading.
        "cta_text": text(100),
        "cta_link": LINK,
        "prompt": PROMPT,
    },
    "media_text": {
        "surface": SURFACE,
        "heading": text(200),
        "body": text(5000),
        "eyebrow": text(100),
        "note": text(200),
        # A SUBSTRING of ``body`` set in the emphasis weight — not markup:
        # an HTML prop would be an injection surface for one bold phrase.
        "emphasis": text(120),
        "bullets": entries(6, {"text": 300}),
        "specs": SPEC_CARDS,
        "image_url": IMAGE_URL,
        "image_position": choice("left", "right"),
        "cta_text": text(100),
        "cta_link": LINK,
        "decor": choice("none", "orbs", "gradient"),
    },
    "image_gallery": {
        "surface": SURFACE,
        "items": entries(24, {"src": 1000, "alt": 200}, {"caption": text(200)}),
        "columns": integer(2, 4),
    },
    "story_timeline": {
        "heading": text(200),
        "subheading": text(500),
        "items": entries(
            20,
            {"title": 100},
            {"date": text(50), "text": text(500), "icon": icon(100)},
        ),
        "surface": SURFACE,
    },
    "faq": {
        "surface": SURFACE,
        "heading": text(200),
        "subheading": text(500),
        "items": entries(30, {"question": 200, "answer": 2000}),
        "multiple": BOOLEAN,
    },
    # How you pay, who delivers, and — for a store that answers agents —
    # that it is agent-readable.
    "trust_badges": {
        "surface": SURFACE,
        "heading": text(200),
        "items": BADGES,
        # A marquee is for a strip too long to fit a phone.
        "marquee": BOOLEAN,
    },
    # Live promotions on a page that is not /offers; renders nothing
    # when promotions are off or none are running.
    "offers_preview": {
        "surface": SURFACE,
        "heading": text(200),
        "subheading": text(500),
        "limit": integer(1, 6),
        "cta_text": text(100),
        "cta_link": LINK,
    },
    # The proof row as a band of its own, for a page whose hero is an
    # image or a carousel.
    "stats_strip": {"items": STATS, "surface": SURFACE},
}


def props_schema(component_type: str) -> Schema:
    return obj(SECTION_PROPS[component_type])


def i18n_schema(component_type: str) -> Schema:
    """``{"<locale>": {"title": ..., "props": {...}}}`` for every locale
    but the default: the default's values ARE the section's own
    ``title``/``props``, so a default key would give one locale two
    sources of truth. ``props`` is a partial override merged over the
    section's props at render time, against the same contract."""
    locales = sorted(
        available_language_codes() - {settings.PARLER_DEFAULT_LANGUAGE_CODE}
    )
    override = obj({"title": text(200), "props": props_schema(component_type)})
    return {
        "type": "object",
        "propertyNames": {"enum": locales},
        "additionalProperties": override,
    }
