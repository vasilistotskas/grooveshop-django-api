"""JSON Schemas for ``RecommendationSlot`` (``core.json_schema``).

An unknown strategy code stored in a slot would surface as a
``KeyError`` on every product page, so the codes are an enum of the
registered strategies. That every weighted code is also in the chain is
a cross-field rule (``RecommendationSlot.clean``).
"""

from __future__ import annotations

from typing import Any

from recommendation.enum import StrategyCode


def strategy_chain() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"enum": list(StrategyCode.values)},
        "minItems": 1,
        "uniqueItems": True,
    }


def weights() -> dict[str, Any]:
    """Code -> weight in 0..1; a missing code weighs 1.0 at read time,
    so an empty mapping means unweighted."""
    return {
        "type": "object",
        "propertyNames": {"enum": list(StrategyCode.values)},
        "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
    }
