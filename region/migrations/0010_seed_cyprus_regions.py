"""Seed the 6 Cypriot districts, mirroring ``0009_seed_default_regions``.

Depends on ``country/0012_seed_cyprus`` for its FK target — ``CY`` must
exist before a region can reference it.

IDEMPOTENT AND NON-DESTRUCTIVE — ``get_or_create`` keyed on ``alpha``
(the model's own primary key), never ``update_or_create``. Reverse is
a no-op, same reasoning as ``0009_seed_default_regions``.
"""

from __future__ import annotations

from django.db import migrations

# (alpha, sort_order, el name, en name)
CYPRUS_REGIONS = [
    ("CY-01", 1, "Λευκωσία", "Nicosia"),
    ("CY-02", 2, "Λεμεσός", "Limassol"),
    ("CY-03", 3, "Λάρνακα", "Larnaca"),
    ("CY-04", 4, "Αμμόχωστος", "Famagusta"),
    ("CY-05", 5, "Πάφος", "Paphos"),
    ("CY-06", 6, "Κερύνεια", "Kyrenia"),
]

COUNTRY_ALPHA_2 = "CY"
SEED_LANGUAGES = ("el", "en")


def seed_cyprus_regions(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    Region = apps.get_model("region", "Region")
    RegionTranslation = apps.get_model("region", "RegionTranslation")
    db_alias = schema_editor.connection.alias

    try:
        country = Country.objects.using(db_alias).get(alpha_2=COUNTRY_ALPHA_2)
    except Country.DoesNotExist:
        # The country seed migration is a dependency and should have
        # already created this row; if it hasn't (e.g. an operator
        # deleted CY), there is nothing sensible to attach a region to.
        return

    for alpha, sort_order, name_el, name_en in CYPRUS_REGIONS:
        names = {"el": name_el, "en": name_en}
        region, created = Region.objects.using(db_alias).get_or_create(
            alpha=alpha,
            defaults={"country": country, "sort_order": sort_order},
        )
        if not created:
            # An operator already owns this row — leave it alone.
            continue

        for language_code in SEED_LANGUAGES:
            RegionTranslation.objects.using(db_alias).get_or_create(
                master=region,
                language_code=language_code,
                defaults={"name": names[language_code]},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("region", "0009_seed_default_regions"),
        ("country", "0012_seed_cyprus"),
    ]

    operations = [
        migrations.RunPython(
            seed_cyprus_regions,
            # Reverse is a no-op: the rows are indistinguishable from
            # operator-created ones, and deleting one that an address
            # or shipping zone already references would break that
            # reference.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
