"""One ``Vat`` row per rate: ``vat_value_unique``.

The standard rate's row is named by its value
(``vat.constants.STANDARD_VAT_RATE``), and ``Product.vat`` points at a row, so
two rows of one rate made every "the 24% row" lookup a guess (the demo-store
seed and ``product`` 0068 both had to take the oldest).

Existing duplicates abort this migration
========================================

``AddUniqueConstraintAdaptively`` counts them first and raises an
``IntegrityError`` naming the duplicated values and the schema; nothing is
changed, the PreSync hook stops before the new image rolls and the old pods
keep serving. It never merges rows: two rows of one rate may each carry
products, and which one stays is a person's call (move the products of one to
the other in the admin, then delete the empty row). To list them for a tenant
schema::

    SELECT value, count(*), array_agg(id ORDER BY id)
    FROM vat_vat GROUP BY value HAVING count(*) > 1;

Every tenant on staging and production held exactly 0, 6, 13 and 24 once each
when this shipped (checked 2026-11-08), so it applies cleanly there.

The table is tiny, so the build is instant; the helper is used for its
duplicate refusal, which a plain ``AddConstraint`` does not give (Postgres'
own error names a value and no schema). The constraint carries the
condition ``value IS NOT NULL`` (always true: the column is NOT NULL) because
the helper builds a partial unique index.
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("vat", "0009_field_verbose_names"),
    ]

    operations = [
        AddUniqueConstraintAdaptively(
            "vat",
            models.UniqueConstraint(
                fields=["value"],
                condition=models.Q(value__isnull=False),
                name="vat_value_unique",
                violation_error_message="A VAT rate with this value already exists.",
            ),
        ),
    ]
