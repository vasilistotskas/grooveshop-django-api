"""The content pages every store starts with, and the state of its legal ones.

Provisioning seeds them (``seed_content_pages``): the legal documents with the
platform's text, published, and the other policy pages as prompts for the
merchant. The rest reads the current schema: which languages each legal
document can be served in, and which pages lack a platform text update.
"""

from __future__ import annotations

import logging

from django.conf import settings

from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    LEGAL_TEXT_REVISION,
    LEGAL_TEXT_UPDATES,
    LegalTextUpdate,
    pending_legal_updates,
    render_legal_document,
)
from page_config.models import ContentPage, ContentPageTranslation

logger = logging.getLogger(__name__)


# Universal store-policy placeholders, every tenant gets these.
# Unpublished on creation — a merchant
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
    are never overwritten. A legal document it creates carries the
    current platform text, so it is stamped ``LEGAL_TEXT_REVISION``.
    Returns ``{slug: created}``.
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
            defaults={
                "is_published": content["published"],
                "legal_text_revision": (
                    LEGAL_TEXT_REVISION
                    if slug in LEGAL_DOCUMENT_SLUGS
                    else None
                ),
            },
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


def pending_legal_reviews() -> list[tuple[ContentPage, LegalTextUpdate]]:
    """Legal pages here that lack a platform update, with the update.

    Reads the CURRENT schema. A page is listed while its
    ``legal_text_revision`` is below an update to its slug: the rollout
    could not apply the update because the text is the merchant's, so
    the merchant has to. Marking the page reviewed in the admin clears
    it. Unpublished pages count too — publishing one later must not
    publish a stale document.
    """
    slugs = {update.slug for update in LEGAL_TEXT_UPDATES}
    pages = ContentPage.objects.filter(slug__in=slugs).order_by("slug")
    return [
        (page, update)
        for page in pages
        for update in pending_legal_updates(page.slug, page.legal_text_revision)
    ]
