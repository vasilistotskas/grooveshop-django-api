"""A validator path stored as data must always resolve.

``src/settings/store_settings.py`` names each store setting's validator by a
dotted path, and ``tenant.settings_metadata.sync_setting_metadata`` writes
it into every store's ``extra_settings.Setting`` row on each migrate. A
path that no longer imports turns an admin save of that setting into a
500 (``Setting.validate()`` raises ``ValueError``). ``order``'s validators
are also reachable at a permanent address, ``order.setting_validators``,
so the stored paths can point there while the modules that define them
move.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.utils.module_loading import import_string

from order.communications import review_request
from order.inventory import stock
from order.payments import payment_hold

ORDER_VALIDATORS = {
    "validate_hold_minutes_setting": payment_hold,
    "validate_max_hold_minutes_setting": payment_hold,
    "validate_review_request_delay_days_setting": review_request,
    "validate_reservation_ttl_minutes_setting": stock,
}


@pytest.mark.parametrize("name", sorted(ORDER_VALIDATORS))
def test_the_permanent_address_names_the_defining_function(name):
    assert import_string(f"order.setting_validators.{name}") is getattr(
        ORDER_VALIDATORS[name], name
    )


def test_every_stored_validator_path_imports():
    paths = {
        row["name"]: row["validator"]
        for row in settings.EXTRA_SETTINGS_DEFAULTS
        if row.get("validator")
    }
    assert paths
    for name, path in paths.items():
        assert callable(import_string(path)), name


def test_every_order_validator_is_stored_at_the_permanent_address():
    """A path stored as data cannot move in one release, so an ``order``
    validator is named at ``order.setting_validators``, never at the
    module that defines it."""
    stored = [
        row["validator"]
        for row in settings.EXTRA_SETTINGS_DEFAULTS
        if str(row.get("validator") or "").startswith("order.")
    ]
    assert stored
    assert all(
        path.startswith("order.setting_validators.") for path in stored
    ), stored
