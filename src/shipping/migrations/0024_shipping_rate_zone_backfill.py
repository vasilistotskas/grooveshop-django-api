"""Put every shipping rate in a zone: one whole-country zone per country.

Each country a store has rates for gets one zone that covers the whole
country (no regions), named after the country (its default-language
name), and its rates are put in it. A whole-country zone covers every
region, so what each store serves and charges does not change. The
reverse gives every rate its country back and deletes the zones. It
refuses a store whose rates are in a zone with regions: the old shape has
one rate per (provider, country, kind) and no way to say "Crete", so
reversing would lose those prices or break that constraint. Delete those
zones' rates first.
"""

from django.conf import settings
from django.db import migrations


def zone_every_rate(apps, schema_editor):
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    ShippingZone = apps.get_model("shipping", "ShippingZone")
    CountryTranslation = apps.get_model("country", "CountryTranslation")

    countries = (
        ShippingRate.objects.filter(zone__isnull=True)
        .order_by()
        .values_list("country_id", flat=True)
        .distinct()
    )
    for country_id in countries:
        name = (
            CountryTranslation.objects.filter(
                master_id=country_id,
                language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE,
            )
            .values_list("name", flat=True)
            .first()
        )
        zone, _created = ShippingZone.objects.get_or_create(
            country_id=country_id,
            regions__isnull=True,
            defaults={"name": name or country_id},
        )
        ShippingRate.objects.filter(
            country_id=country_id, zone__isnull=True
        ).update(zone=zone)


def unzone_every_rate(apps, schema_editor):
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    ShippingZone = apps.get_model("shipping", "ShippingZone")
    ShippingZoneRegion = apps.get_model("shipping", "ShippingZoneRegion")

    region_zones = ShippingZoneRegion.objects.values("zone_id")
    priced = (
        ShippingZone.objects.filter(pk__in=region_zones, rates__isnull=False)
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

    for rate in ShippingRate.objects.filter(country__isnull=True).select_related(
        "zone"
    ):
        rate.country_id = rate.zone.country_id
        rate.save(update_fields=["country"])
    ShippingRate.objects.update(zone=None)
    ShippingZone.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0023_shipping_rate_zone"),
    ]

    operations = [
        migrations.RunPython(zone_every_rate, unzone_every_rate),
    ]
