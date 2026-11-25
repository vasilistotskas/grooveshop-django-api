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
from django.db.models import F, Q

logger = logging.getLogger(__name__)


def _oldest_base_zone(apps, db, country_id):
    """The country's base zone, the oldest if a store somehow has two:
    only the admin form keeps it to one."""
    return (
        apps.get_model("shipping", "ShippingZone")
        .objects.using(db)
        .filter(country_id=country_id, regions__isnull=True)
        .order_by("pk")
        .first()
    )


def _base_zone(apps, db, country_id):
    """:func:`_oldest_base_zone`, or a new one named after the country,
    under a name no other zone has."""
    ShippingZone = apps.get_model("shipping", "ShippingZone")
    CountryTranslation = apps.get_model("country", "CountryTranslation")

    zone = _oldest_base_zone(apps, db, country_id)
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
    """Put each rate in the base zone of the country it carries, where it
    is not already in a zone that country's rates are written for.

    The first run zones every rate. The contract release runs it again
    for the rates the release before wrote during the rollout: one it
    inserted (no zone), and one it re-pointed to another country (a
    country other than the one this release writes for its zone,
    ``ShippingZone.rate_country_id``: the zone's own for a base zone, none
    for a region zone). A rate whose (provider, kind) the base zone
    already prices is that rate's duplicate, never chargeable as written:
    it is deleted and logged, rather than failing the migration on the
    unique ``(provider, zone, kind)``. History rows get their country's
    base zone too, where it has one.
    """
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    HistoricalShippingRate = apps.get_model(
        "shipping", "HistoricalShippingRate"
    )
    ShippingZoneRegion = apps.get_model("shipping", "ShippingZoneRegion")
    db = schema_editor.connection.alias
    schema = schema_editor.connection.schema_name

    region_zones = ShippingZoneRegion.objects.using(db).values("zone_id")
    misplaced = ShippingRate.objects.using(db).filter(
        Q(zone__isnull=True)
        | Q(zone__in=region_zones)
        | ~Q(zone__country_id=F("country_id")),
        country__isnull=False,
    )
    for country_id in list(
        misplaced.order_by().values_list("country_id", flat=True).distinct()
    ):
        zone = _base_zone(apps, db, country_id)
        moving = misplaced.filter(country_id=country_id).exclude(zone=zone)
        priced = set(
            ShippingRate.objects.using(db)
            .filter(zone=zone)
            .exclude(pk__in=moving.values("pk"))
            .values_list("provider_id", "kind")
        )
        duplicates = []
        for rate in moving:
            key = (rate.provider_id, rate.kind)
            if key in priced:
                duplicates.append(rate.pk)
            else:
                priced.add(key)
        if duplicates:
            logger.warning(
                "Deleted %d shipping rates of %s in schema %s: zone %s "
                "already has a rate for their provider and kind (ids %s)",
                len(duplicates),
                country_id,
                schema,
                zone.pk,
                duplicates,
            )
            ShippingRate.objects.using(db).filter(pk__in=duplicates).delete()
        moving.update(zone=zone)

    for country_id in list(
        HistoricalShippingRate.objects.using(db)
        .filter(zone__isnull=True, country__isnull=False)
        .order_by()
        .values_list("country_id", flat=True)
        .distinct()
    ):
        zone = _oldest_base_zone(apps, db, country_id)
        if zone is not None:
            HistoricalShippingRate.objects.using(db).filter(
                zone__isnull=True, country_id=country_id
            ).update(zone=zone)


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
