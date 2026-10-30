"""Store settings as the merchant leaves them, for tests through the API.

The suite reads ``Setting.get`` straight from the database (the
extra-settings cache is switched off in ``tests/conftest.py``), so a row
written here is what every gate and service sees, inside the test's
transaction.
"""

from __future__ import annotations

from extra_settings.models import Setting


def switch_store_settings(**values: bool) -> None:
    """Turn boolean store switches on or off, e.g. ``LOYALTY_ENABLED=True``."""
    for name, value in values.items():
        Setting.objects.update_or_create(
            name=name, defaults={"value_type": "bool", "value_bool": value}
        )


def switch_every_feature_on() -> None:
    """Every setting an optional feature needs, on, so each of its pages
    is shown (``admin.features``). A test that walks every admin page
    needs it: a page of a feature that is not live and holds no rows is
    refused."""
    from admin.features import FEATURES

    switch_store_settings(
        **{
            gate.setting_key: True
            for feature in FEATURES.values()
            for gate in feature.settings
        }
    )
