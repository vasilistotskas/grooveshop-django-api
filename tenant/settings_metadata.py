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
never touched: a row whose current value a new validator refuses keeps it,
and the admin asks for a valid one at the next edit.
"""

from django.conf import settings


def sync_setting_metadata(**kwargs) -> int:
    """Align existing rows' description and validator; return rows changed.

    Wired to ``post_migrate`` for the extra_settings app (``TenantConfig.
    ready``), alongside the library's own receiver that creates missing rows.
    """
    from extra_settings.cache import del_cached_setting
    from extra_settings.models import Setting

    changed = 0
    for item in settings.EXTRA_SETTINGS_DEFAULTS:
        description = item.get("description", "")
        validator = item.get("validator")
        rows = Setting.objects.filter(name=item["name"]).exclude(
            description=description, validator=validator
        )
        updated = rows.update(description=description, validator=validator)
        if updated:
            del_cached_setting(item["name"])
            changed += updated
    return changed
