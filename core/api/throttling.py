from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class ContactCreateThrottle(AnonRateThrottle):
    scope = "contact"


class PaymentAttemptThrottle(UserRateThrottle):
    scope = "payment"


class PaymentAttemptAnonThrottle(AnonRateThrottle):
    scope = "payment_anon"


class CartMutationThrottle(UserRateThrottle):
    scope = "cart_mutation"


class CartMutationAnonThrottle(AnonRateThrottle):
    scope = "cart_mutation_anon"


class SearchThrottle(AnonRateThrottle):
    scope = "search"


class ViewCountThrottle(AnonRateThrottle):
    """Tight per-IP throttle for the product view-count increment endpoint."""

    scope = "view_count"


class VivaReturnThrottle(AnonRateThrottle):
    """Per-IP throttle for the anonymous Viva hosted-checkout return
    resolver. The global anon limit (100k/day) is far too loose for an
    AllowAny lookup that echoes order id/uuid/status — cap it tightly."""

    scope = "viva_return"
