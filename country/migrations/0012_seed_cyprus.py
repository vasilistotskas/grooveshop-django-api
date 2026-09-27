"""Seed Cyprus so webside can offer BoxNow-lockers-only delivery there.

Follows the ``0010_seed_default_country`` pattern: ``get_or_create``
keyed on ``alpha_2`` (the model's own primary key), never
``update_or_create``, so an operator-edited row is left untouched.
``sort_order`` is placed after every existing row (``max + 1``), so a
fresh environment still lists Greece first and keeps it the default.

The postal-code pattern/example match ``0011_country_postal_code_format``'s
Google Address Data Service entry for CY (``\\d{4}``, ``2008``) — that
migration already ran and only fills a row that exists *at that time*,
so this later-created row needs the same values set explicitly.

IDEMPOTENT AND NON-DESTRUCTIVE, reverse is a no-op — same reasoning as
``0010_seed_default_country``: deleting the row could cascade into a
region or shipping rate an operator has since attached to it.
"""

from __future__ import annotations

from django.db import migrations
from django.db.models import Max

# (alpha_2, alpha_3, iso_cc, phone_code, postal pattern, postal example, el name, en name)
CYPRUS = (
    "CY",
    "CYP",
    196,
    357,
    r"\d{4}",
    "2008",
    "Κύπρος",
    "Cyprus",
)

SEED_LANGUAGES = ("el", "en")


def seed_cyprus(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    CountryTranslation = apps.get_model("country", "CountryTranslation")
    db_alias = schema_editor.connection.alias

    (
        alpha_2,
        alpha_3,
        iso_cc,
        phone_code,
        postal_code_pattern,
        postal_code_example,
        name_el,
        name_en,
    ) = CYPRUS
    names = {"el": name_el, "en": name_en}

    max_sort_order = Country.objects.using(db_alias).aggregate(
        Max("sort_order")
    )["sort_order__max"]
    sort_order = (max_sort_order or 0) + 1

    country, created = Country.objects.using(db_alias).get_or_create(
        alpha_2=alpha_2,
        defaults={
            "alpha_3": alpha_3,
            "iso_cc": iso_cc,
            "phone_code": phone_code,
            "sort_order": sort_order,
            "postal_code_pattern": postal_code_pattern,
            "postal_code_example": postal_code_example,
        },
    )
    if not created:
        # An operator already owns this row — leave every field alone.
        return

    for language_code in SEED_LANGUAGES:
        CountryTranslation.objects.using(db_alias).get_or_create(
            master=country,
            language_code=language_code,
            defaults={"name": names[language_code]},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("country", "0011_country_postal_code_format"),
    ]

    operations = [
        migrations.RunPython(
            seed_cyprus,
            # Reverse is a no-op: the row is indistinguishable from an
            # operator-created one, and deleting it would cascade into
            # any Region or ShippingRate referencing Cyprus.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
