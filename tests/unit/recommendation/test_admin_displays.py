"""Every enum value the event admin renders has a pill variant.

A missing key renders an unstyled chip rather than failing, so a new
choice slips through silently — which is exactly what happened when
``AttachMatch.IMPRESSION`` was added (2026-09-11) and the map still
held only cart and user. Mirrors the pay_way precedent
(``tests/unit/pay_way/test_pay_way_admin.py``).
"""

from __future__ import annotations

import pytest

from recommendation.admin import (
    ATTACH_MATCH_VARIANT,
    EVENT_KIND_VARIANT,
    STRATEGY_VARIANT,
)
from recommendation.enum import AttachMatch, EventKind, StrategyCode


@pytest.mark.parametrize(
    ("choices", "variants"),
    [
        (AttachMatch, ATTACH_MATCH_VARIANT),
        (EventKind, EVENT_KIND_VARIANT),
        (StrategyCode, STRATEGY_VARIANT),
    ],
)
def test_every_choice_has_a_display_variant(choices, variants):
    missing = [choice.value for choice in choices if choice not in variants]
    assert not missing, f"no admin pill variant for: {missing}"
