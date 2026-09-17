from __future__ import annotations

import logging

from django.conf import settings

from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    LEGAL_ROUTE_BY_SLUG,
    render_legal_document,
)
from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    PageLayout,
    PageSection,
)

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
    # products/blog deliberately have NO default layout.
    #
    # The storefront treats a published layout for these page types as an
    # optional BRANDED BAND rendered above the page's own content, with
    # an empty fallback so a tenant without one renders exactly as it
    # does today (app/pages/products/index.vue, app/pages/blog/index.vue).
    # Seeding them with full listing sections therefore duplicated the
    # page: products_grid mounts its own <ProductsList>, so a freshly
    # provisioned tenant showed a search bar and an unfiltered grid, THEN
    # the real breadcrumb, sidebar and product list — two lists competing
    # over the same URL filter state. The blog page got two post grids.
    #
    # A band is for brand content (a hero, an announcement), not a second
    # copy of the listing the page already renders. Operators can publish
    # one from the admin when they actually want it.
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
    # The banner is a traffic path, not decoration: it linked to this
    # product before the homepage moved onto the builder, and
    # HeroCarousel only renders the wrapping link when a ``link`` prop is
    # present — without it the promo became a dead image.
    "link": "/products/2/mini-powerbank-5000mah",
}

# Footer navigation for the brand store, published alongside the pages
# it points at.
#
# The storefront's code-level footer fallback carries only links every
# store has (about, legal, contact). These brand-specific columns used to
# live in that fallback, so EVERY tenant's footer advertised this store's
# product concept and linked to /vision, /what-is-microlearning and
# /why-microlearning — pages that render an empty body for any tenant
# without a published layout. Seeding them here keeps this store's
# chrome identical while leaving other tenants' footers clean.
#
# Labels are literals, not translation keys: a NavigationMenu row IS the
# operator's content, and the admin edits it as such.
BRAND_FOOTER_COLUMNS: list[dict] = [
    {
        "label": "Σχετικά με εμάς",
        "icon": "i-heroicons-information-circle",
        "children": [
            {"label": "Σχετικά με το Webside", "to": "/about"},
            {"label": "Όραμα", "to": "/vision"},
        ],
    },
    {
        "label": "Microlearning",
        "icon": "i-heroicons-light-bulb",
        "children": [
            {
                "label": "Τι είναι το Microlearning",
                "to": "/what-is-microlearning",
            },
            {"label": "Γιατί Microlearning", "to": "/why-microlearning"},
        ],
    },
    {
        "label": "Όροι & Προϋποθέσεις",
        "icon": "i-heroicons-rectangle-group",
        "children": [
            {"label": "Όροι Χρήσης", "to": "/terms-of-use"},
            {"label": "Πολιτική Απορρήτου", "to": "/privacy-policy"},
            {"label": "Πολιτική Cookies", "to": "/cookies-policy"},
        ],
    },
    {
        "label": "Κέντρο Βοήθειας",
        "icon": "i-heroicons-chat-bubble-left-right",
        "children": [
            {"label": "Επικοινωνία", "to": "/contact"},
        ],
    },
]

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

    Run AFTER provisioning, which is where it sits in practice: the
    footer links resolve to the rows they point at, so the legal
    ContentPages ``seed_content_pages`` creates must already exist or
    those links are skipped (and logged) rather than stored as paths.
    """
    created_map: dict[str, bool] = {}

    # Publish the footer that points at these pages. Without it the
    # storefront falls back to its universal columns and this store
    # silently loses its Vision and Microlearning links.
    from page_config.models import NavigationMenu, NavigationSlot

    footer, footer_created = NavigationMenu.objects.get_or_create(
        slot=NavigationSlot.FOOTER
    )
    created_map["footer_navigation"] = footer_created

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
    # Built LAST, and as rows rather than the JSON this used to write:
    # `localized()` reads columns and links now, so a seeded blob would
    # give the store a footer that silently renders nothing. Last
    # because a link resolves to the PageLayout it points at, and those
    # are created above — seeded earlier, every brand link would find
    # no target and be skipped.
    if footer_created:
        links = build_navigation_menu(footer, BRAND_FOOTER_COLUMNS)
        logger.info("Seeded brand footer navigation (%s links)", links)

    return created_map


# Universal store-policy placeholders, every tenant gets these (unlike the
# opt-in ``BRAND_PAGE_LAYOUTS`` above). Unpublished on creation — a merchant
# reviews and writes the real content before it goes live; the slug + Greek
# placeholder title/body just mark where each page belongs. Body is plain
# HTML (not markdown) to match ``ContentPageTranslation.body``'s TinyMCE
# field.
# Pages seeded as an EMPTY PROMPT for the merchant to fill. Only the
# slugs whose content nobody but the merchant can write live here: the
# three legal documents are seeded with the platform's real text from
# ``legal_documents.py`` instead, because a store must not go live
# without terms, privacy and a cookie policy.
DEFAULT_CONTENT_PAGES: dict[str, dict[str, str]] = {
    "return-policy": {
        "title": "Πολιτική Επιστροφών",
        "body": "<p>Προσθέστε εδώ την πολιτική επιστροφών του καταστήματός σας.</p>",
    },
    "faq": {
        "title": "Συχνές Ερωτήσεις",
        "body": "<p>Προσθέστε εδώ τις συχνές ερωτήσεις των πελατών σας.</p>",
    },
    "about": {
        "title": "Σχετικά με εμάς",
        "body": "<p>Προσθέστε εδώ πληροφορίες σχετικά με το κατάστημά σας.</p>",
    },
    "shipping-info": {
        "title": "Πληροφορίες Αποστολής",
        "body": "<p>Προσθέστε εδώ τις πληροφορίες αποστολής του καταστήματός σας.</p>",
    },
}


def tenant_document_context() -> tuple[str, str]:
    """Resolve ``(site_host, store_name)`` for the tenant being seeded.

    The same two values the storefront bound into the legal templates as
    ``siteHost`` / ``storeName``, resolved the same way: the primary
    domain row, and the customer-facing store name.

    Both fall back rather than raise. Seeding runs inside tenant
    provisioning, and a missing domain row must not leave a new store
    without a terms page — it leaves one sentence reading oddly, which
    the merchant can fix in the admin.
    """
    from django.db import connection

    schema_name = getattr(connection, "schema_name", "") or ""
    site_host = ""
    store_name = ""

    # Resolved from the SCHEMA NAME, not from ``connection.tenant``.
    # That attribute is a real Tenant inside ``tenant_context`` but not
    # while ``migrate_schemas`` runs, where it carries no ``domains`` —
    # which is how migration 0021 seeded all four live tenants the
    # APP_MAIN_HOST_NAME fallback instead of their own hosts (corrected
    # by 0022). The schema name is right in both contexts.
    if schema_name and schema_name != getattr(
        settings, "PUBLIC_SCHEMA_NAME", "public"
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(t.store_name, ''), t.name),
                       COALESCE(d.domain, '')
                  FROM public.tenant_tenant t
                  LEFT JOIN public.tenant_tenantdomain d
                         ON d.tenant_id = t.id AND d.is_primary
                 WHERE t.schema_name = %s
                 LIMIT 1
                """,
                [schema_name],
            )
            row = cursor.fetchone()
        if row:
            store_name, site_host = row[0] or "", row[1] or ""

    if not site_host:
        site_host = getattr(settings, "APP_MAIN_HOST_NAME", "") or ""
        logger.warning(
            "No primary domain for schema %r while seeding legal pages; "
            "fell back to %r",
            schema_name or "?",
            site_host,
        )
    if not store_name:
        store_name = getattr(settings, "SITE_NAME", "") or ""

    return site_host, store_name


def seed_content_pages() -> dict[str, bool]:
    """Create each tenant's content pages if they don't exist.

    Two kinds, and the difference is deliberate:

    - The three legal documents (``LEGAL_DOCUMENT_SLUGS``) are seeded
      with the platform's real text and **published**. A storefront must
      not go live without terms, a privacy policy and a cookie policy,
      and since 2026-09-17 the routes render these rows rather than
      markup compiled into the storefront — so an unpublished row is a
      missing legal page, not a blank one.
    - Everything else is seeded unpublished with a prompt, because
      nobody but the merchant can write it.

    Idempotent (``get_or_create`` by slug) — safe to run repeatedly, and
    it never touches a row that already exists, so a merchant's edits
    are never overwritten. Returns ``{slug: created}``.
    """
    site_host, store_name = tenant_document_context()

    seeds: dict[str, dict[str, str | bool]] = {
        slug: {
            "title": LEGAL_DOCUMENTS[slug]["title"],
            "body": render_legal_document(
                slug, site_host=site_host, store_name=store_name
            ),
            "published": True,
        }
        for slug in LEGAL_DOCUMENT_SLUGS
    }
    seeds.update(
        {
            slug: {
                "title": content["title"],
                "body": content["body"],
                "published": False,
            }
            for slug, content in DEFAULT_CONTENT_PAGES.items()
        }
    )

    created_map: dict[str, bool] = {}
    for slug, content in seeds.items():
        page, created = ContentPage.objects.get_or_create(
            slug=slug,
            defaults={"is_published": content["published"]},
        )
        if created:
            ContentPageTranslation.objects.create(
                master=page,
                language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE,
                title=content["title"],
                body=content["body"],
            )
            logger.info("Seeded content page: %s", slug)
        created_map[slug] = created
    return created_map


def legal_translation_coverage() -> dict[str, set[str]]:
    """Which locales each legal document is actually usable in, here.

    Reads the CURRENT schema, so callers switch tenant first. A
    translation row that exists with an empty body counts as missing:
    the legal routes 404 on an empty body, so a blank row is exactly as
    unreachable as no row at all — and parler will not fall back past a
    row that exists, so nothing rescues it.
    """
    coverage: dict[str, set[str]] = {}
    rows = ContentPageTranslation.objects.filter(
        master__slug__in=LEGAL_DOCUMENT_SLUGS,
        master__is_published=True,
    ).values_list("master__slug", "language_code", "body")
    for slug, language_code, body in rows:
        if body and body.strip():
            coverage.setdefault(slug, set()).add(language_code)
    return coverage


def _navigation_target(path: str) -> dict | None:
    """Which typed target a declarative spec's path refers to.

    Seeds are written as paths because that is how a person describes a
    menu, but they are STORED as targets — the whole point of the
    relational menus is that nothing keeps a hand-typed path around to
    rot. A path that matches nothing returns ``None`` and the caller
    skips it rather than inventing a link.
    """
    from page_config.models import BuiltInRoute, ContentPage, PageLayout

    if path.startswith(("http://", "https://")):
        return {"url": path}
    for slug, canonical in LEGAL_ROUTE_BY_SLUG.items():
        if path == canonical:
            page = ContentPage.objects.filter(slug=slug).first()
            if page is not None:
                return {"content_page": page}
    if path.startswith("/info/"):
        page = ContentPage.objects.filter(slug=path[len("/info/") :]).first()
        if page is not None:
            return {"content_page": page}
    layout = PageLayout.objects.filter(page_type=path.lstrip("/")).first()
    if layout is not None:
        return {"page_layout": layout}
    if path in set(BuiltInRoute.values):
        return {"route": path}
    return None


def build_navigation_menu(
    menu, spec: list[dict], i18n: dict[str, list] | None = None
) -> int:
    """Turn a declarative menu spec into columns and links.

    One implementation for every seeder, so a store built by
    ``seed_brand_pages`` and one built by the demo seeder cannot end up
    with differently-shaped menus. The footer's spec is a list of
    columns; header and mobile are a flat list of links.

    ``i18n`` is the same spec per non-default locale, index-aligned
    with ``spec`` because that is how the JSON menus expressed it: a
    whole-menu copy. Only the LABELS are taken from it — a translation
    cannot retarget a link — and a copy whose shape differs is ignored
    rather than mis-assigned.

    Returns how many links were created. Callers pass a freshly created
    or emptied menu — this does not reconcile an existing one.
    """
    from page_config.models import (
        NavigationColumn,
        NavigationColumnTranslation,
        NavigationLink,
        NavigationLinkTranslation,
        NavigationSlot,
    )

    language = settings.PARLER_DEFAULT_LANGUAGE_CODE
    overrides = i18n or {}
    created = 0

    def _labels_at(*path) -> dict[str, str]:
        """``{locale: label}`` for one position in the menu tree."""
        found: dict[str, str] = {}
        for locale, blob in overrides.items():
            cursor: object = blob
            for key in path:
                if isinstance(cursor, list) and isinstance(key, int):
                    cursor = cursor[key] if key < len(cursor) else None
                elif isinstance(cursor, dict) and isinstance(key, str):
                    cursor = cursor.get(key)
                else:
                    cursor = None
                if cursor is None:
                    break
            if isinstance(cursor, dict) and cursor.get("label"):
                found[locale] = cursor["label"]
        return found

    def _add_link(
        entry: dict,
        *,
        index: int,
        spec_path: tuple,
        column=None,
        parent=None,
    ) -> None:
        nonlocal created
        path = entry.get("to") or entry.get("href") or ""
        target = _navigation_target(path)
        if target is None:
            logger.warning("Navigation seed: no target for %r", path)
            return
        link = NavigationLink.objects.create(
            menu=parent,
            column=column,
            sort_order=index,
            content_page=target.get("content_page"),
            page_layout=target.get("page_layout"),
            route=target.get("route", ""),
            url=target.get("url", ""),
        )
        created += 1
        # A page link takes the page's own translated title; storing a
        # copy here would freeze it at seed time.
        if target.get("content_page") is None and entry.get("label"):
            NavigationLinkTranslation.objects.create(
                master=link, language_code=language, label=entry["label"]
            )
            for locale, label in _labels_at(*spec_path).items():
                NavigationLinkTranslation.objects.create(
                    master=link, language_code=locale, label=label
                )

    if menu.slot == NavigationSlot.FOOTER:
        for i, column_spec in enumerate(spec):
            column = NavigationColumn.objects.create(
                menu=menu, sort_order=i, icon=column_spec.get("icon") or ""
            )
            NavigationColumnTranslation.objects.create(
                master=column,
                language_code=language,
                label=column_spec.get("label") or "",
            )
            for locale, label in _labels_at(i).items():
                NavigationColumnTranslation.objects.create(
                    master=column, language_code=locale, label=label
                )
            for j, child in enumerate(column_spec.get("children") or []):
                _add_link(
                    child,
                    index=j,
                    spec_path=(i, "children", j),
                    column=column,
                )
    else:
        for i, entry in enumerate(spec):
            _add_link(entry, index=i, spec_path=(i,), parent=menu)

    return created
