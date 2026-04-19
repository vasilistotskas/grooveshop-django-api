from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class ContactCreateThrottle(AnonRateThrottle):
    scope = "contact"


class PaymentAttemptThrottle(UserRateThrottle):
    scope = "payment"


class PaymentAttemptAnonThrottle(AnonRateThrottle):
    scope = "payment_anon"
