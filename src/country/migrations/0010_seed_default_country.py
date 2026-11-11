"""Seed Greece so a fresh environment's country table is not empty.

``country`` is a SHARED_APPS model (public schema only — it is not
listed in TENANT_APPS) and had no data migration, so a freshly
provisioned environment starts with zero rows: no country to attach an
address, a shipping zone, or a region to. Mirrors the ``vat``/
``pay_way``/``loyalty`` seed migrations, which close the same kind of
gap for their own (TENANT_APPS) tables.

Seeded from the production ``country_country`` row for Greece (natural
PK ``alpha_2``). ``image_flag`` is deliberately left unset — the flag
image lives on production media storage and would not exist on a
fresh environment; an operator can upload one afterwards without this
migration ever touching the row again.

IDEMPOTENT AND NON-DESTRUCTIVE — ``get_or_create`` keyed on
``alpha_2`` (the model's own primary key), never ``update_or_create``:
an existing environment may already have operator-edited country data
(a corrected phone code, a re-ordered ``sort_order``), and rewriting it
would silently change checkout/shipping behaviour built on it. This
migration only ever ADDS the row when missing.
"""

from __future__ import annotations

from django.db import migrations

# (alpha_2, alpha_3, iso_cc, phone_code, sort_order, el name)
DEFAULT_COUNTRY = ("GR", "GRC", 297, 30, 0, "Ελλάδα")

SEED_LANGUAGE = "el"


def seed_default_country(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    CountryTranslation = apps.get_model("country", "CountryTranslation")
    db_alias = schema_editor.connection.alias

    alpha_2, alpha_3, iso_cc, phone_code, sort_order, name = DEFAULT_COUNTRY
    country, created = Country.objects.using(db_alias).get_or_create(
        alpha_2=alpha_2,
        defaults={
            "alpha_3": alpha_3,
            "iso_cc": iso_cc,
            "phone_code": phone_code,
            "sort_order": sort_order,
        },
    )
    if not created:
        # An operator already owns this row — leave every field alone.
        return

    CountryTranslation.objects.using(db_alias).get_or_create(
        master=country,
        language_code=SEED_LANGUAGE,
        defaults={"name": name},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("country", "0009_alter_country_phone_code"),
    ]

    operations = [
        migrations.RunPython(
            seed_default_country,
            # Reverse is a no-op: the row is indistinguishable from an
            # operator-created one, and deleting it would cascade into
            # every Region (and anything else) referencing Greece.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
