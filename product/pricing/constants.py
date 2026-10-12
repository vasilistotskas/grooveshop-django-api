"""The windows the EU Omnibus "prior price" rule is written in.

Directive 98/6/EC art. 6a as transposed in Greece (art. 9ι of Law
2251/1994) and Cyprus (art. 45 of Law 112(I)/2021). They are statutory
numbers, not deployment settings: a tenant must not be able to shorten
them, so they live here rather than in ``settings.py``.
"""

from __future__ import annotations

from datetime import timedelta

#: A reduction is announced against the lowest price of this many days
#: BEFORE it started, and a product on sale for less than this long may
#: not announce one at all (the stricter Cypriot rule, applied
#: everywhere so no per-country switch exists).
REFERENCE_WINDOW = timedelta(days=30)

#: Progressive uninterrupted reductions keep the reference from before
#: the first one, but only for this long after the first.
PROGRESSIVE_REDUCTION_WINDOW = timedelta(days=60)
