"""Put every shipping rate in a zone: one whole-country zone per country.

Expand step of moving ``ShippingRate.country`` onto ``ShippingRate.zone``
(``docs/migrations.md``):

1. ``country`` becomes nullable: the new code stops writing it. First,
   so the reverse makes it NOT NULL again only after it has been filled.
2. ``zone`` is added nullable: the release still serving during the
   rollout inserts rates without it.
3. Each country a store has rates for gets one zone that covers the whole
   country (no regions), named after the country, and its rates are put
   in it. A whole-country zone covers every region, so what each store
   serves and charges does not change.

``0024`` takes ``country`` out of the state; the contract release drops
it after copying any rate the old release wrote meanwhile.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


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
    # The rows written queue their deferred foreign-key checks; run them
    # now, so a later ALTER of this table in the same transaction (a store
    # provisioned in one) does not meet "pending trigger events".
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


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
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0022_shipping_zone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shippingrate",
            name="country",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="country.country",
                verbose_name="Country",
            ),
        ),
        migrations.AddField(
            model_name="historicalshippingrate",
            name="zone",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="shipping.shippingzone",
                verbose_name="Shipping Zone",
            ),
        ),
        migrations.AddField(
            model_name="shippingrate",
            name="zone",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rates",
                to="shipping.shippingzone",
                verbose_name="Shipping Zone",
            ),
        ),
        migrations.RunPython(zone_every_rate, unzone_every_rate),
    ]
