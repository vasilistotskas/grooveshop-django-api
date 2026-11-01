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
# A band that is drawn dark: the ink surface a promotional band sits on.
# Only the sections that have a dark treatment take it.
INK_SURFACE = choice("default", "muted", "ink")


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
            # The product this slide features, drawn as a chip over the
            # artwork. The id, not a slug (a slug is translatable); that
            # it exists in this store is checked in Python.
            "product_id": ID,
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
        # ``enum: [0]``, not ``const``: the API publishes these as
        # OpenAPI 3.0, which has no ``const``.
        "autoplay_ms": {"oneOf": [{"enum": [0]}, integer(3000, 15000)]},
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
    # The guest card's own wording; the numbers and tiers on it come
    # from the loyalty settings, never from here.
    "loyalty_hero": {
        "surface": SURFACE,
        "eyebrow": text(100),
        "cta_text": text(100),
        "cta_link": LINK,
        "secondary_cta_text": text(100),
        "secondary_cta_link": LINK,
    },
    "search_bar": {},
    "about_content": {},
    "vision_content": {},
    "what_is_microlearning": {},
    "why_microlearning": {},
    # Data comes from the BUSINESS_HOURS setting.
    "business_hours": {"surface": SURFACE},
    # The pin is the store's own location, the STORE_GEO_LAT / STORE_GEO_LNG
    # settings its structured data reads too (migration 0035 moved the
    # coordinates sections used to carry).
    "location_map": {
        "surface": SURFACE,
        "embed_url": HTTPS_URL,
        "address": text(300),
    },
    "partner_strip": {
        "label": text(80),
        "items": entries(12, {"name": 60}, {"href": LINK}),
    },
    # The contact page as one band. The offices come from the
    # STORE_OFFICES setting and the form labels are UI, so neither is here.
    "contact_panel": {
        "eyebrow": text(100),
        "heading": text(200),
        "body": text(600),
        "hint": text(400),
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
        "surface": INK_SURFACE,
        "eyebrow": text(100),
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
