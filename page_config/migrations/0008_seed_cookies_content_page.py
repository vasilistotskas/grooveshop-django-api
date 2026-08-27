"""Backfill the ``cookies`` content page for existing tenants.

The storefront ships a ``/cookies-policy`` route alongside
``/terms-of-use`` and ``/privacy-policy``, and those two routes now
prefer a merchant's own published ContentPage over the platform
boilerplate. ``cookies`` had no seeded slug, so it was the one legal
page a merchant could NOT override — stuck with platform text published
under their name.

``0007`` seeded the original set and carries its own frozen copy of the
defaults, so tenants provisioned before this change never receive the
new entry from ``page_config.defaults``. This backfills them.

Unpublished on creation, like every other seeded page: it marks where
the page belongs, and the merchant writes and publishes the real
content. Until they do, the storefront keeps rendering the boilerplate,
so behaviour is unchanged for stores that do nothing.
"""

from django.conf import settings
from django.db import migrations

SLUG = "cookies"
TITLE = "Πολιτική Cookies"
BODY = "<p>Προσθέστε εδώ την πολιτική cookies του καταστήματός σας.</p>"


def seed_cookies_page(apps, schema_editor):
    from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

    # ``page_config`` is TENANT_APPS-only; the public schema holds no
    # store content and must not gain a stray row.
    if schema_editor.connection.schema_name == get_public_schema_name():
        return

    ContentPage = apps.get_model("page_config", "ContentPage")
    ContentPageTranslation = apps.get_model(
        "page_config", "ContentPageTranslation"
    )
    db_alias = schema_editor.connection.alias

    page, created = ContentPage.objects.using(db_alias).get_or_create(
        slug=SLUG,
        defaults={"is_published": False},
    )
    if created:
        ContentPageTranslation.objects.using(db_alias).create(
            master=page,
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE,
            title=TITLE,
            body=BODY,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("page_config", "0007_seed_content_pages"),
    ]

    operations = [
        migrations.RunPython(
            seed_cookies_page,
            # Reverse is a no-op: by the time anyone rolls back, the
            # merchant may have written and published real cookie-policy
            # text into this row. Deleting it would destroy their
            # content to undo a placeholder.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
