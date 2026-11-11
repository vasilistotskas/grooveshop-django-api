"""Which payment provider a gift-card purchase defaults to.

Its own module rather than a literal in the serializer AND another in
the view: the two disagreed silently once the string appeared twice, and
the request serializer's ``default=`` has to be the same value the view
falls back to when the member is absent.

Not a capability — every registered provider can sell a gift card. This
is a preference, so it stays a plain constant rather than something a
provider declares about itself.
"""

from __future__ import annotations

#: Matches ``StripePaymentProvider.code``. Intent-first, so the buyer
#: confirms the card inline instead of leaving for a hosted page —
#: fewer steps for the common case.
DEFAULT_GIFT_CARD_PROVIDER = "stripe"
