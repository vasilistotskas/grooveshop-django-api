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
