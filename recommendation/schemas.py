"""Shape checks for the JSON configuration on ``RecommendationSlot``.

Plain-Python validators raising ``ValidationError``, the same way
``page_config/schemas.py`` guards ``NavigationMenu.items``. They run
from the model's ``clean()`` and from the admin's ``save_model`` so a
bad chain can never be stored — an unknown strategy code in a slot
would otherwise surface as a ``KeyError`` on every product page.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from recommendation.enum import StrategyCode

_KNOWN_CODES = frozenset(StrategyCode.values)


def validate_strategy_chain(value: Any) -> list[str]:
    """A non-empty, duplicate-free list of ``StrategyCode`` values.

    Returns the validated list so callers can normalise in one step.
    """
    if not isinstance(value, list):
        raise ValidationError(_("The strategy chain must be a list."))
    if not value:
        raise ValidationError(
            _("The strategy chain must name at least one strategy.")
        )

    seen: set[str] = set()
    for index, code in enumerate(value):
        if not isinstance(code, str) or code not in _KNOWN_CODES:
            raise ValidationError(
                _("Entry %(index)s is not a known strategy: %(code)r.")
                % {"index": index, "code": code}
            )
        if code in seen:
            raise ValidationError(
                _("Strategy %(code)s appears more than once.") % {"code": code}
            )
        seen.add(code)
    return list(value)


def validate_weights(value: Any, chain: list[str]) -> dict[str, float]:
    """A ``{code: weight}`` mapping; codes ⊆ chain, weights in ``0..1``.

    Codes absent from the mapping default to ``1.0`` at read time, so an
    empty dict is valid and means "unweighted".
    """
    if not isinstance(value, dict):
        raise ValidationError(_("Weights must be a mapping."))

    allowed = set(chain)
    cleaned: dict[str, float] = {}
    for code, weight in value.items():
        if code not in allowed:
            raise ValidationError(
                _("Weight given for %(code)r, which is not in the chain.")
                % {"code": code}
            )
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise ValidationError(
                _("Weight for %(code)s must be a number.") % {"code": code}
            )
        if not 0.0 <= float(weight) <= 1.0:
            raise ValidationError(
                _("Weight for %(code)s must be between 0 and 1.")
                % {"code": code}
            )
        cleaned[code] = float(weight)
    return cleaned
