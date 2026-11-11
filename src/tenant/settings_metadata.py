"""Keep every store's extra-setting rows describing the setting it has now.

django-extra-settings copies a setting's ``description`` and ``validator``
from ``EXTRA_SETTINGS_DEFAULTS`` into the row only when it CREATES the row
(``Setting.set_defaults``), and validates against the path stored on the
row. A store provisioned before a description was corrected therefore kept
the old text in its admin, and one provisioned before a validator was added
kept accepting what the validator refuses: ``STORE_GEO_LAT`` took any string
in every store that existed when its validator arrived.

Both are the platform's metadata, never the merchant's, so they follow
``settings.py`` on every ``migrate`` (each deploy's PreSync runs
``migrate_schemas``, which migrates every store's schema). The value is
never touched: a row whose current value a newly attached validator refuses
keeps it, is reported in the log, and the admin asks for a valid one at the
next edit.

``value_type`` is deliberately NOT synced: extra_settings keeps each value
in a column per type, so changing the type of an existing row would orphan
its value. A type change ships as a data migration that converts the
value (``shipping/migrations/0011_convert_legacy_pricing_to_rates.py``).
"""

import logging

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


def sync_setting_metadata(**kwargs) -> int:
    """Align existing rows' description and validator; return rows changed.

    Wired to ``post_migrate`` for the extra_settings app (``TenantConfig.
    ready``), alongside the library's own receiver that creates missing rows.
    """
    from extra_settings.cache import del_cached_setting
    from extra_settings.models import Setting
    from extra_settings.utils import import_function

    changed = 0
    for item in settings.EXTRA_SETTINGS_DEFAULTS:
        description = item.get("description", "")
        validator = item.get("validator")
        stale = list(
            Setting.objects.filter(name=item["name"]).exclude(
                description=description, validator=validator
            )
        )
        if not stale:
            continue
        Setting.objects.filter(pk__in=[row.pk for row in stale]).update(
            description=description, validator=validator
        )
        del_cached_setting(item["name"])
        changed += len(stale)
        if validator is None:
            continue
        check = import_function(validator)
        for row in stale:
            if row.validator != validator and not check(row.value):
                # The name and the store, never the value: a setting may
                # hold a credential.
                logger.warning(
                    "Setting %s in schema %s fails its new validator %s; "
                    "its value is kept, and the admin asks for a valid one "
                    "at the next edit.",
                    row.name,
                    connection.schema_name,
                    validator,
                )
    return changed
