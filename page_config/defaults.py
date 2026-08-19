from __future__ import annotations

import logging

from page_config.models import PageLayout, PageSection

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LAYOUTS: dict[str, dict] = {
    # Mirrors the platform homepage (and the Nuxt FALLBACK_LAYOUTS.home
    # safety net) exactly: blog categories rail → main banner carousel →
    # recently-viewed rail → blog posts list. Seeding the real page —
    # instead of a generic marketing shape — means a freshly provisioned
    # tenant (and webside at cutover) starts from today's proven layout
    # and customizes from there.
    "home": {
        "title": "Homepage",
        "sections": [
            {
                "component_type": "blog_categories",
                "title": "",
                "props": {},
            },
            {
                "component_type": "hero_carousel",
                "title": "",
                "props": {},
            },
            {
                "component_type": "recently_viewed",
                "title": "",
                "props": {},
            },
            {
                "component_type": "blog_posts_list",
                "title": "",
                "props": {},
            },
        ],
    },
    "products": {
        "title": "Products Page",
        "sections": [
            {
                "component_type": "search_bar",
                "title": "",
                "props": {},
            },
            {
                "component_type": "products_grid",
                "title": "",
                "props": {"page_size": 12},
            },
        ],
    },
    "blog": {
        "title": "Blog Page",
        "sections": [
            {
                "component_type": "blog_posts_grid",
                "title": "Latest Posts",
                "props": {"count": 12},
            },
        ],
    },
}


def seed_page_layouts() -> None:
    """Create default page layouts if they don't exist.

    Called during tenant provisioning — every tenant gets these.
    """
    for page_type, config in DEFAULT_PAGE_LAYOUTS.items():
        layout, created = PageLayout.objects.get_or_create(
            page_type=page_type,
            defaults={
                "title": config["title"],
                "is_published": True,
            },
        )
        if created:
            for section_data in config["sections"]:
                PageSection.objects.create(
                    layout=layout,
                    **section_data,
                )
            logger.info("Seeded page layout: %s", page_type)


# Brand-specific marketing/content pages. Each section's rendering is a
# per-tenant Nuxt variant component with no props — the markup itself
# stays in the frontend, these rows only carry the page/section shape.
#
# Deliberately NOT part of ``DEFAULT_PAGE_LAYOUTS``/``seed_page_layouts``
# (which every tenant gets on creation): these are opt-in, seeded only
# for tenants that actually ship these specific pages via
# ``manage.py seed_brand_pages --schema <schema>`` (see
# MULTI_TENANT_CUTOVER.md §0.3/§6 for the webside cutover step). No
# tenant name is hardcoded here or in the command — the caller picks
# the schema.
# Banner artwork for the brand store's homepage hero, applied by
# ``seed_brand_pages()``. The shared HeroCarousel component deliberately
# has no built-in banner (a hardcoded default would put this store's
# promo on every tenant whose layout carries a prop-less hero_carousel —
# observed live on the staging tenant #2), so the artwork lives here as
# SECTION DATA and the universal default seed's prop-less hero renders
# nothing.
BRAND_HOME_HERO_PROPS: dict = {
    "images": ["/img/main-banner.png"],
    "mobile_images": ["/img/main-banner-mobile.png"],
}

BRAND_PAGE_LAYOUTS: dict[str, dict] = {
    "about": {
        "title": "About",
        "sections": [
            {
                "component_type": "about_content",
                "title": "",
                "props": {},
            },
        ],
    },
    "vision": {
        "title": "Vision",
        "sections": [
            {
                "component_type": "vision_content",
                "title": "",
                "props": {},
            },
        ],
    },
    "what-is-microlearning": {
        "title": "What Is Microlearning",
        "sections": [
            {
                "component_type": "what_is_microlearning",
                "title": "",
                "props": {},
            },
        ],
    },
    "why-microlearning": {
        "title": "Why Microlearning",
        "sections": [
            {
                "component_type": "why_microlearning",
                "title": "",
                "props": {},
            },
        ],
    },
}


def seed_brand_pages() -> dict[str, bool]:
    """Create the brand marketing page layouts if they don't exist.

    Idempotent — safe to run repeatedly. Returns ``{page_type:
    created}`` so the caller (``manage.py seed_brand_pages``) can
    report what happened. Must be called inside the target tenant's
    schema (e.g. via ``django_tenants.utils.schema_context``) — this
    function itself has no notion of which schema it's running in.
    """
    created_map: dict[str, bool] = {}
    for page_type, config in BRAND_PAGE_LAYOUTS.items():
        layout, created = PageLayout.objects.get_or_create(
            page_type=page_type,
            defaults={
                "title": config["title"],
                "is_published": True,
            },
        )
        if created:
            for section_data in config["sections"]:
                PageSection.objects.create(
                    layout=layout,
                    **section_data,
                )
            logger.info("Seeded brand page layout: %s", page_type)
        created_map[page_type] = created

    # Home hero artwork: ensure the home layout exists (create it from
    # the universal default when absent) and fill a PROP-LESS
    # hero_carousel with the brand banner props. A hero that already
    # carries props was customized by the merchant — left untouched.
    home_config = DEFAULT_PAGE_LAYOUTS["home"]
    home, home_created = PageLayout.objects.get_or_create(
        page_type="home",
        defaults={
            "title": home_config["title"],
            "is_published": True,
        },
    )
    if home_created:
        for section_data in home_config["sections"]:
            PageSection.objects.create(layout=home, **section_data)
        logger.info("Seeded page layout: home (via brand seeding)")
    hero = home.sections.filter(component_type="hero_carousel").first()
    if hero is not None and not hero.props:
        hero.props = dict(BRAND_HOME_HERO_PROPS)
        hero.save(update_fields=["props"])
        logger.info("Applied brand banner props to the home hero")
    created_map["home"] = home_created
    return created_map
