"""Give every product without a VAT rate the store's standard rate.

``Product.vat`` used to be optional, and deleting a ``Vat`` row set it to NULL
(``SET_NULL``). A product with no rate is priced, snapshotted onto its order
lines and invoiced at 0%, which is a wrong Greek invoice, so the field is now
required on every write path and its rate cannot be deleted (0069).

Runs once per tenant schema (django-tenants migrates each one). A ``Vat`` row
carries only its ``value``, so the standard row is the one holding the Greek
standard rate, myDATA ``vatCategory`` 1 (``vat.constants.STANDARD_VAT_RATE``,
copied here because a migration must not move when that constant does). The
rows are seeded by ``vat`` 0007.

FAILS LOUDLY, naming the schema, when a store has products without a rate and
no standard row to give them. Inventing a row would be a tax decision, so an
operator decides instead: ``manage.py mydata_readiness`` reports the products.

The products' gross price rises by the standard rate (they were sold at 0%).
The rows are updated in place, not saved, so the price records and the search
documents catch up in the nightly ``reevaluate_price_episodes`` sweep.

Not reversible: once filled, a rate that was backfilled cannot be told from
one a merchant chose.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import migrations

logger = logging.getLogger(__name__)

STANDARD_VAT_RATE = Decimal("24.0")


def set_standard_vat(apps, schema_editor):
    Product = apps.get_model("product", "Product")
    Vat = apps.get_model("vat", "Vat")
    alias = schema_editor.connection.alias
    schema = getattr(schema_editor.connection, "schema_name", alias)

    # ``_base_manager``: a soft-deleted product is restored with its rate.
    missing = Product._base_manager.using(alias).filter(vat__isnull=True)
    count = missing.count()
    if not count:
        logger.info("product 0068 [%s]: every product has a VAT rate", schema)
        return

    standard = (
        Vat.objects.using(alias).filter(value=STANDARD_VAT_RATE).first()
    )
    if standard is None:
        raise RuntimeError(
            f"Schema {schema!r} has {count} product(s) without a VAT rate "
            f"and no {STANDARD_VAT_RATE}% (standard) VAT row to give them. "
            "Add the row, or set the products' rate, then migrate again."
        )

    updated = missing.update(vat=standard)
    logger.info(
        "product 0068 [%s]: %s product(s) without VAT set to %s%%",
        schema,
        updated,
        STANDARD_VAT_RATE,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0067_image_title_drop_columns"),
        ("vat", "0009_field_verbose_names"),
    ]

    operations = [
        migrations.RunPython(
            set_standard_vat, migrations.RunPython.noop, elidable=False
        ),
    ]
