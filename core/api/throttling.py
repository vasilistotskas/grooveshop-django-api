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


class AcsAddressValidationThrottle(AnonRateThrottle):
    """Per-IP throttle for the public ACS address-validation proxy, which
    forwards to the rate-limited ACS partner API (G0016)."""

    scope = "acs_address"


class BoxNowNearestThrottle(AnonRateThrottle):
    """Per-IP throttle for the public BoxNow nearest-locker proxy, which
    forwards synchronously to the BoxNow partner API (G0059)."""

    scope = "boxnow_nearest"
