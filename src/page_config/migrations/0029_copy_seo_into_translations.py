"""Copy each row's SEO fields into its translation in the authored language.

``ContentPage, PageLayout`` carried ``seo_title`` / ``seo_description`` /
``seo_keywords`` on the shared row, so a bilingual store served one
language's ``<title>`` and meta description on every locale's URL.
``0028_seo_translated_fields`` renamed those columns to ``shared_seo_*`` and
added the real fields to the parler translations model; this moves
the values across, and ``0030_remove_shared_seo_fields`` drops the shared
columns.

**Which language.** The old columns were never translated, so the
operator wrote them in the store's own language: ``Tenant.default_locale``
of the schema being migrated — not the platform-wide
``PARLER_DEFAULT_LANGUAGE_CODE``, which a store authoring in another
language does not use. Under ``migrate_schemas``, django-tenants'
``run_migrations`` (``django_tenants/migration_executors/base.py``) calls
``connection.set_schema(schema_name)``, which installs a ``FakeTenant``
carrying only ``schema_name`` (``postgresql_backend/base.py``
``set_schema``) with ``public`` still on the search path — so
``connection.tenant`` is NOT the tenant row, and the row is read from
``tenant.Tenant`` by ``connection.schema_name`` instead. A schema with
rows to copy but no tenant row is an error, not a guess.

**Which rows.** Only a row with at least one non-empty SEO value; an
empty one needs no translation row. The translation in that language is
updated when it exists and created when it does not.

Parler's documented procedure for making existing fields translatable
(add the translated fields, copy with a data migration, remove the
originals — django-parler "Making existing fields translatable") is the
shape of the three migrations. Like ``page_config/0021``, this keeps its
logic local rather than importing app code that may change shape later.

Reversible: backwards writes the authored-language translation's values
back onto the shared row, once ``0030_remove_shared_seo_fields``'s reverse has restored
the columns.
"""

from __future__ import annotations

from django.db import migrations
from django.db.models import Q

APP_LABEL = "page_config"
# (shared model, its translations model)
MODELS = (
    ("ContentPage", "ContentPageTranslation"),
    ("PageLayout", "PageLayoutTranslation"),
)
SEO_FIELDS = ("seo_title", "seo_description", "seo_keywords")
# The shared-row columns, renamed by the previous migration.
SHARED_FIELDS = {f"shared_{name}": name for name in SEO_FIELDS}
HAS_SHARED_SEO = (
    ~Q(shared_seo_title="")
    | ~Q(shared_seo_description="")
    | ~Q(shared_seo_keywords="")
)
HAS_SEO = ~Q(seo_title="") | ~Q(seo_description="") | ~Q(seo_keywords="")


def _authored_language(apps, connection) -> str:
    """``default_locale`` of the tenant whose schema is being migrated."""
    Tenant = apps.get_model("tenant", "Tenant")
    schema_name = connection.schema_name
    locale = (
        Tenant.objects.filter(schema_name=schema_name)
        .values_list("default_locale", flat=True)
        .first()
    )
    if not locale:
        raise RuntimeError(
            f"Schema {schema_name!r} has SEO values to move but no tenant "
            "row, so the language they were written in is unknown."
        )
    return locale


def copy_seo_into_translations(apps, schema_editor):
    language = None
    for model_name, translation_name in MODELS:
        model = apps.get_model(APP_LABEL, model_name)
        translation = apps.get_model(APP_LABEL, translation_name)
        rows = model._base_manager.filter(HAS_SHARED_SEO).values(
            "pk", *SHARED_FIELDS
        )
        for row in rows.iterator():
            language = language or _authored_language(
                apps, schema_editor.connection
            )
            master_id = row.pop("pk")
            translation._base_manager.update_or_create(
                master_id=master_id,
                language_code=language,
                defaults={SHARED_FIELDS[key]: value for key, value in row.items()},
            )


def copy_seo_back_to_shared_row(apps, schema_editor):
    language = None
    for model_name, translation_name in MODELS:
        model = apps.get_model(APP_LABEL, model_name)
        translation = apps.get_model(APP_LABEL, translation_name)
        rows = translation._base_manager.filter(HAS_SEO).values(
            "master_id", "language_code", *SEO_FIELDS
        )
        for row in rows.iterator():
            language = language or _authored_language(
                apps, schema_editor.connection
            )
            if row.pop("language_code") != language:
                continue
            master_id = row.pop("master_id")
            model._base_manager.filter(pk=master_id).update(
                **{f"shared_{name}": value for name, value in row.items()}
            )


class Migration(migrations.Migration):
    dependencies = [
        ("page_config", "0028_seo_translated_fields"),
        ("tenant", "0041_tenant_google_ads_conversion"),
    ]

    operations = [
        migrations.RunPython(
            copy_seo_into_translations, copy_seo_back_to_shared_row
        ),
    ]
