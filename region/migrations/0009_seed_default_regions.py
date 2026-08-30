"""Seed the 12 Greek regions so a fresh environment has somewhere for
an address or shipping zone to point at.

``region`` is a SHARED_APPS model (public schema only) and had no data
migration, mirroring the gap ``country/migrations/
0010_seed_default_country`` closes for its own table. Depends on that
migration for its FK target — ``GR`` must exist before a region can
reference it.

IDEMPOTENT AND NON-DESTRUCTIVE — ``get_or_create`` keyed on ``alpha``
(the model's own primary key), never ``update_or_create``: an existing
environment may already have operator-edited region data, and
rewriting it would silently change shipping-zone/address behaviour
built on it. This migration only ever ADDS missing rows.
"""

from __future__ import annotations

from django.db import migrations

# (alpha, sort_order, el name)
DEFAULT_REGIONS = [
    ("GR-14", 1, "Αττική"),
    ("GR-17", 2, "Κεντρική Μακεδονία"),
    ("GR-6", 3, "Θεσσαλία"),
    ("GR-8", 4, "Δυτική Ελλάδα"),
    ("GR-13", 5, "Κρήτη"),
    ("GR-10", 6, "Πελοπόννησος"),
    ("GR-15", 7, "Αν. Μακεδονία & Θράκη"),
    ("GR-9", 8, "Στερεά Ελλάδα"),
    ("GR-5", 9, "Ήπειρος"),
    ("GR-4", 10, "Δυτική Μακεδονία"),
    ("GR-7", 11, "Ιόνιες Νήσοι"),
    ("GR-12", 12, "Αιγαίο"),
]

COUNTRY_ALPHA_2 = "GR"
SEED_LANGUAGE = "el"


def seed_default_regions(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    Region = apps.get_model("region", "Region")
    RegionTranslation = apps.get_model("region", "RegionTranslation")
    db_alias = schema_editor.connection.alias

    try:
        country = Country.objects.using(db_alias).get(alpha_2=COUNTRY_ALPHA_2)
    except Country.DoesNotExist:
        # The country seed migration is a dependency and should have
        # already created this row; if it hasn't (e.g. an operator
        # deleted GR), there is nothing sensible to attach a region to.
        return

    for alpha, sort_order, name in DEFAULT_REGIONS:
        region, created = Region.objects.using(db_alias).get_or_create(
            alpha=alpha,
            defaults={"country": country, "sort_order": sort_order},
        )
        if not created:
            # An operator already owns this row — leave it alone.
            continue

        RegionTranslation.objects.using(db_alias).get_or_create(
            master=region,
            language_code=SEED_LANGUAGE,
            defaults={"name": name},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("region", "0008_alter_region_sort_order"),
        ("country", "0010_seed_default_country"),
    ]

    operations = [
        migrations.RunPython(
            seed_default_regions,
            # Reverse is a no-op: the rows are indistinguishable from
            # operator-created ones, and deleting one that an address
            # or shipping zone already references would break that
            # reference.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
