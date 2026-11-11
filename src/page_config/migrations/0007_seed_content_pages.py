"""Backfill the default ContentPage placeholders into every EXISTING tenant.

Tenants provisioned AFTER this migration get the same defaults via
``tenant.provisioning.seed_tenant_defaults`` ->
``page_config.defaults.seed_content_pages``; this migration applies the
identical idempotent set to tenants provisioned before ``ContentPage``
existed — including the live ``webside`` tenant — so every tenant
converges on one set regardless of when it was created.

The default data is duplicated here rather than imported from
``page_config.defaults`` (mirrors ``shipping/migrations/0002_seed_
providers.py`` and siblings): a historical migration must not depend
on live app code that can change shape later.

django-tenants runs page_config migrations once per TENANT schema via
``migrate_schemas`` (page_config is TENANT_APPS-only, never SHARED —
see ``settings.py``), so a plain ``RunPython`` here is enough; no
explicit schema loop is needed. The public-schema guard is defensive:
page_config has no tables there today, but skipping is a cheap no-op
if that classification ever changes.

Idempotent (``get_or_create`` by slug) and safe to re-run.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations

DEFAULT_CONTENT_PAGES: dict[str, dict[str, str]] = {
    "return-policy": {
        "title": "Πολιτική Επιστροφών",
        "body": "<p>Προσθέστε εδώ την πολιτική επιστροφών του καταστήματός σας.</p>",
    },
    "terms": {
        "title": "Όροι Χρήσης",
        "body": "<p>Προσθέστε εδώ τους όρους χρήσης του καταστήματός σας.</p>",
    },
    "privacy": {
        "title": "Πολιτική Απορρήτου",
        "body": "<p>Προσθέστε εδώ την πολιτική απορρήτου του καταστήματός σας.</p>",
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


def seed_content_pages(apps, schema_editor):
    from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

    if schema_editor.connection.schema_name == get_public_schema_name():
        return

    ContentPage = apps.get_model("page_config", "ContentPage")
    ContentPageTranslation = apps.get_model(
        "page_config", "ContentPageTranslation"
    )
    db_alias = schema_editor.connection.alias
    default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

    for slug, content in DEFAULT_CONTENT_PAGES.items():
        page, created = ContentPage.objects.using(db_alias).get_or_create(
            slug=slug,
            defaults={"is_published": False},
        )
        if created:
            ContentPageTranslation.objects.using(db_alias).create(
                master=page,
                language_code=default_language,
                title=content["title"],
                body=content["body"],
            )


def unseed_content_pages(apps, schema_editor):
    from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

    if schema_editor.connection.schema_name == get_public_schema_name():
        return

    ContentPage = apps.get_model("page_config", "ContentPage")
    ContentPage.objects.using(schema_editor.connection.alias).filter(
        slug__in=DEFAULT_CONTENT_PAGES
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("page_config", "0006_contentpage_contentpagetranslation"),
    ]

    operations = [
        migrations.RunPython(
            seed_content_pages, reverse_code=unseed_content_pages
        ),
    ]
