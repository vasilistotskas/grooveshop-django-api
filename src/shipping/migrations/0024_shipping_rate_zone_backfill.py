"""Put every shipping rate in a zone: one whole-country zone per country.

Each country a store has rates for gets one zone that covers the whole
country (no regions), named after the country (its default-language
name), and its rates are put in it. A whole-country zone covers every
region, so what each store serves and charges does not change. The
reverse gives every rate its country back and deletes the zones.
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
