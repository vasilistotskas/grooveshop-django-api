"""Seed the standard Greek VAT rates so a fresh tenant has usable rates.

``vat`` is TENANT_APPS-only and had no data migration, so every new
tenant starts with an empty table (verified: the staging tenant
``aurora`` has none). Two consequences:

* The product WRITE API is blocked outright. Both product serializers
  declare ``vat = PrimaryKeyRelatedField(queryset=Vat.objects.all())``
  with no ``required=False`` — an explicitly-declared DRF relation is
  required regardless of the model's ``null=True`` — so with no rows
  every POST/PUT/PATCH to /api/v1/product/ fails validation. (That
  mismatch is fixed alongside this migration; seeding alone would leave
  it re-breakable by deleting the rows.)
* A product saved through the admin with ``vat=None`` prices at 0% and
  is invoiced at 0%, which is a silently wrong Greek invoice.

Rates seeded are the mainland set that ``order/mydata/builder.py``
recognises: 24 (standard), 13 and 6 (reduced), and 0 (zero-rated /
exempt). The island-discounted bands (17/9/4) and the Law 5057/2023 3%
band are real but merchant-specific, so they stay an admin decision
rather than noise in every tenant.

IDEMPOTENT AND NON-DESTRUCTIVE — ``get_or_create`` keyed on ``value``,
never ``update_or_create``. Existing tenants own live rows referenced by
priced products, and rewriting them would change what customers are
charged and what appears on issued invoices. This migration only ever
ADDS missing rates; it never edits or removes one, and it never
reassigns a product.

NOTE for whoever reads this next: production currently holds a single
rate of 23.0, which stopped being the Greek standard rate in June 2016
and is absent from ``_VAT_CATEGORY_BY_RATE``, so it will raise
``ValueError: Unsupported VAT rate 23%`` the moment myDATA is enabled
(it is off today). This migration deliberately does NOT touch it —
moving products from 23% to 24% changes prices and invoices and is a
tax decision for the merchant. Seeding 24 simply makes the correct rate
available to move to.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import migrations

# Mainland Greek rates recognised by order/mydata/builder.py.
DEFAULT_VAT_RATES = [
    Decimal("24.0"),  # standard
    Decimal("13.0"),  # reduced
    Decimal("6.0"),  # super-reduced
    Decimal("0.0"),  # zero-rated / exempt
]


def seed_vat_rates(apps, schema_editor):
    Vat = apps.get_model("vat", "Vat")
    db_alias = schema_editor.connection.alias

    for value in DEFAULT_VAT_RATES:
        Vat.objects.using(db_alias).get_or_create(value=value)


class Migration(migrations.Migration):
    dependencies = [
        ("vat", "0006_rename_vat_created_at_idx_vat_created_at_ix_and_more"),
    ]

    operations = [
        migrations.RunPython(
            seed_vat_rates,
            # Reverse is a no-op: these rows are indistinguishable from
            # merchant-created ones, and deleting a Vat that a product
            # points at would null the product's rate (SET_NULL) and
            # silently reprice it at 0%.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
