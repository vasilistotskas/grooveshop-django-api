"""The one reading of the spelling of a uuid that opens something.

A guest order is opened by its uuid (``Order.opens_for_guest``), which
accepts only the canonical spelling; the throttle that budgets a
gateway's payment calls per order reads the spelling the same way. So
both callers enforce one spelling rule: a spelling the order refuses
(upper case, no hyphens, braces) is never charged to the order it names.
That is spelling parity only. The access window of a guest link
(``settings.GUEST_ORDER_ACCESS_DAYS``) is enforced by
``Order.opens_for_guest`` alone: the throttle does not read it, and may
charge the budget of an order whose link has expired.
"""

from __future__ import annotations

import uuid


def canonical_uuid(raw: object) -> uuid.UUID | None:
    """The uuid ``raw`` spells, when ``raw`` is its canonical form
    (lower case, hyphenated, the form ``str(uuid)`` and the URL converter
    give); ``None`` for anything else, however it would parse."""
    try:
        parsed = uuid.UUID(str(raw))
    except ValueError:
        return None
    return parsed if str(parsed) == str(raw) else None
