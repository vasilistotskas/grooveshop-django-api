"""Drop the promotion window sweep's old cache cursor.

The cursor moved to ``core.SweepCursor`` (``core/sweeps.py``). The cache
key was written with no expiry, so nothing else would remove it. Runs in
every store's schema, where the cache's key function scopes the key to
the store, as it scoped the write. A cache that is unreachable reads as
done (it fails open); the key is then a few bytes left behind.
"""

from django.core.cache import cache
from django.db import migrations


def drop_cursor(apps, schema_editor):
    cache.delete("promotion-offer:window-cursor")


class Migration(migrations.Migration):
    dependencies = [
        ("promotion", "0006_remove_german_translations"),
    ]

    operations = [
        migrations.RunPython(drop_cursor, migrations.RunPython.noop),
    ]
