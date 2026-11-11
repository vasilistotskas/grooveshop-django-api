"""Purged the parler cache entries that 0022's queryset ``.update()`` left.

parler's shared translation cache is off (``PARLER_ENABLE_CACHING``), so no
entry exists to purge and nothing is left to do. The purge read the current
``PayWay`` model, whose later columns a fresh schema does not have at this
point, so on every new store and test database it failed and logged a warning.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0022_boxnow_potg_distinct_name"),
    ]

    operations = []
