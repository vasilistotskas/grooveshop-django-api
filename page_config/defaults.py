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

    Called during tenant provisioning.
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
