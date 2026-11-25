"""Put every shipping rate in a zone: one whole-country zone per country.

Each country a store has rates for gets one zone that covers the whole
country (no regions), named after the country (its default-language
name; with the country code added when another zone has that name), and
its rates are put in it. A country that already has a base zone keeps
it (the oldest, if there are two). A whole-country zone covers every
region, so what each store serves and charges does not change. The
reverse gives every rate its country back and deletes the zones. It
refuses a store whose rates are in a zone with regions: the old shape has
one rate per (provider, country, kind) and no way to say "Crete", so
reversing would lose those prices or break that constraint. Delete those
zones' rates first.
"""

import logging

from django.conf import settings
from django.db import migrations

logger = logging.getLogger(__name__)


def _base_zone(apps, db, country_id):
    """The country's base zone, the oldest if a store somehow has two
    (only the admin form keeps it to one), or a new one named after the
    country, under a name no other zone has."""
    ShippingZone = apps.get_model("shipping", "ShippingZone")
    CountryTranslation = apps.get_model("country", "CountryTranslation")

    zone = (
        ShippingZone.objects.using(db)
        .filter(country_id=country_id, regions__isnull=True)
        .order_by("pk")
        .first()
    )
    if zone is not None:
        return zone
    name = (
        CountryTranslation.objects.using(db)
        .filter(
            master_id=country_id,
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE,
        )
        .values_list("name", flat=True)
        .first()
    ) or country_id
    taken = set(
        ShippingZone.objects.using(db)
        .filter(name__startswith=name)
        .values_list("name", flat=True)
    )
    candidate, attempt = name, 1
    while candidate in taken:
        attempt += 1
        candidate = f"{name} ({country_id})" + (
            f" {attempt - 1}" if attempt > 2 else ""
        )
    return ShippingZone.objects.using(db).create(
        country_id=country_id, name=candidate
    )


def zone_every_rate(apps, schema_editor):
    """Put each rate without a zone in its country's base zone.

    The contract release runs this again for the rates the release before
    shipping zones inserted during the rollout. One whose (provider, kind)
    the base zone already has a rate for is that rate's duplicate, never
    chargeable (``shipping.zones.chargeable`` skips a rate without a
    zone): it is deleted and logged, rather than failing the migration on
    the unique ``(provider, zone, kind)``.
    """
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    db = schema_editor.connection.alias
    schema = schema_editor.connection.schema_name

    countries = (
        ShippingRate.objects.using(db)
        .filter(zone__isnull=True, country__isnull=False)
        .order_by()
        .values_list("country_id", flat=True)
        .distinct()
    )
    for country_id in list(countries):
        zone = _base_zone(apps, db, country_id)
        priced = set(
            ShippingRate.objects.using(db)
            .filter(zone=zone)
            .values_list("provider_id", "kind")
        )
        unzoned = ShippingRate.objects.using(db).filter(
            country_id=country_id, zone__isnull=True
        )
        duplicates = [
            rate.pk
            for rate in unzoned
            if (rate.provider_id, rate.kind) in priced
        ]
        if duplicates:
            logger.warning(
                "Deleted %d shipping rates without a zone in schema %s: "
                "zone %s already has a rate for their provider and kind "
                "in %s (ids %s)",
                len(duplicates),
                schema,
                zone.pk,
                country_id,
                duplicates,
            )
            ShippingRate.objects.using(db).filter(pk__in=duplicates).delete()
        unzoned.update(zone=zone)


def unzone_every_rate(apps, schema_editor):
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    ShippingZone = apps.get_model("shipping", "ShippingZone")
    ShippingZoneRegion = apps.get_model("shipping", "ShippingZoneRegion")
    db = schema_editor.connection.alias

    region_zones = ShippingZoneRegion.objects.using(db).values("zone_id")
    priced = (
        ShippingZone.objects.using(db)
        .filter(pk__in=region_zones, rates__isnull=False)
        .distinct()
        .values_list("name", flat=True)
    )
    if names := list(priced):
        raise RuntimeError(
            f"Cannot reverse in schema {schema_editor.connection.schema_name}:"
            f" the zones {', '.join(sorted(names))} cover regions and hold "
            "rates, which a rate per (provider, country, kind) cannot hold. "
            "Delete those zones' rates first."
        )

    for rate in (
        ShippingRate.objects.using(db)
        .filter(country__isnull=True)
        .select_related("zone")
    ):
        rate.country_id = rate.zone.country_id
        rate.save(using=db, update_fields=["country"])
    ShippingRate.objects.using(db).update(zone=None)
    ShippingZone.objects.using(db).all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0023_shipping_rate_zone"),
    ]

    operations = [
        migrations.RunPython(zone_every_rate, unzone_every_rate),
    ]
