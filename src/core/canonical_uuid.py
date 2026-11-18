"""The one reading of a uuid that opens something.

A guest order is opened by its uuid (``Order.opens_for_guest``), and the
throttle that budgets a gateway's payment calls per order must charge
exactly the requests that authorization can accept. Both go through here,
so a spelling the order refuses (upper case, no hyphens, braces) is never
counted against the order it names.
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
