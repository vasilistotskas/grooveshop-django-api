"""The validators of ``order``'s store settings, at a permanent address.

``src/settings/store_settings.py`` names each store setting's validator by a
dotted path, and ``tenant.settings_metadata.sync_setting_metadata`` writes
that path into every store's ``extra_settings.Setting`` row on each
``migrate``. The release still serving during a deploy reads those rows
back, so a path stored as data cannot change in the same release as the
code it names. This module is the address the data uses: it never moves,
and the functions it names may move freely with their modules.
"""

from order.accounting.movement import (
    validate_loading_postal_code_setting,
    validate_loading_text_setting,
)
from order.accounting.mydata.config import validate_series_prefix_setting
from order.communications.review_request import (
    validate_review_request_delay_days_setting,
)
from order.inventory.stock import validate_reservation_ttl_minutes_setting
from order.payments.payment_hold import (
    validate_grace_minutes_setting,
    validate_hold_minutes_setting,
    validate_max_hold_minutes_setting,
)

__all__ = [
    "validate_grace_minutes_setting",
    "validate_hold_minutes_setting",
    "validate_loading_postal_code_setting",
    "validate_loading_text_setting",
    "validate_max_hold_minutes_setting",
    "validate_reservation_ttl_minutes_setting",
    "validate_review_request_delay_days_setting",
    "validate_series_prefix_setting",
]
