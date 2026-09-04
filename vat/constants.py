"""Single source of truth for which VAT rates myDATA e-invoicing accepts.

Greek standard VAT is 24% (23% stopped being valid in June 2016), and
myDATA B2B e-invoicing becomes mandatory during 2026 — a ``Vat`` row
outside this set builds an invoice fine, then raises ``ValueError``
deep in ``order.mydata.builder._vat_category`` for every order that
uses it, which is how production's stale 23.0 row was found (fixed:
products moved 23%→24%, dead row deleted).

These are the KEYS of ``order/mydata/builder.py``'s
``_VAT_CATEGORY_BY_RATE`` — mirrored here (not imported from there)
because ``order`` depends on ``vat`` (``product.vat`` FKs to it, and
``order`` depends on ``product``); importing ``order.mydata`` back into
``vat`` would invert that dependency. The builder module asserts its
own keys match this set at import time, so the two cannot silently
drift apart.

A rate change in Greek tax law requires a code change here (and in the
builder's category mapping) anyway, so rejecting anything outside this
set at the model-validation layer is the correct, fail-fast behaviour
— not an arbitrary restriction.
"""

from __future__ import annotations

from decimal import Decimal

# Mainland rates (24/13/6/0) plus the island-discount bands (17/9/4)
# and the Law 5057/2023 addition (3) — every rate
# ``order/mydata/builder.py`` can map to an AADE ``vatCategory``.
MYDATA_SUPPORTED_VAT_RATES: frozenset[Decimal] = frozenset(
    {
        Decimal(24),
        Decimal(17),
        Decimal(13),
        Decimal(9),
        Decimal(6),
        Decimal(4),
        Decimal(3),
        Decimal(0),
    }
)
