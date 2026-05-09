from __future__ import annotations

# ---------------------------------------------------------------------------
# BoxNow error-code catalogue
# Source: BoxNow API Manual v7.2, section 5 "Troubleshooting (Error Codes)"
# ---------------------------------------------------------------------------
BOXNOW_ERROR_CODES: dict[str, str] = {
    "P400": (
        "Invalid request data. Make sure you are sending the request"
        " according to the documentation."
    ),
    "P401": (
        "Invalid request origin location reference. Make sure you are"
        " referencing a valid location ID from Origins endpoint or valid"
        " address."
    ),
    "P402": (
        "Invalid request destination location reference. Make sure you are"
        " referencing a valid location ID from Destinations endpoint or"
        " valid address."
    ),
    "P403": (
        "You are not allowed to use AnyAPM-SameAPM delivery. Contact support"
        " if you believe this is a mistake."
    ),
    "P404": "Invalid import CSV. See error contents for additional info.",
    "P405": (
        "Invalid phone number. Make sure you are sending the phone number in"
        " full international format, e.g. +30 xx x xxx xxxx."
    ),
    "P406": (
        "Invalid compartment/parcel size. Make sure you are sending one of"
        " required sizes 1, 2 or 3 (Small, Medium or Large). Size is required"
        " when sending from AnyAPM directly."
    ),
    "P407": (
        "Invalid country code. Make sure you are sending country code in ISO"
        " 3166-1 alpha-2 format, e.g. GR."
    ),
    "P408": (
        "Invalid amountToBeCollected amount. Make sure you are sending amount"
        " in the valid range of (0, 5000)."
    ),
    "P409": (
        "Invalid delivery partner reference. Make sure you are referencing a"
        " valid delivery partner ID from Delivery partners endpoint."
    ),
    "P410": (
        "Order number conflict. You are trying to create a delivery request"
        " for order ID that has already been created. Choose another order ID."
    ),
    "P411": (
        "You are not eligible to use Cash-on-delivery payment type. Use"
        " another payment type or contact our support."
    ),
    "P412": (
        "You are not allowed to create customer returns deliveries. Contact"
        " support if you believe this is a mistake."
    ),
    "P413": (
        "Invalid return location reference. Make sure you are referencing a"
        " valid location warehouse ID from Origins endpoint or valid address."
    ),
    "P414": (
        "Unauthorized parcel access. You are trying to access information to"
        " parcel/s that don't belong to you. Make sure you are requesting"
        " information for parcels you have access to."
    ),
    "P415": (
        "You are not allowed to create delivery to home address. Contact"
        " support if you believe this is a mistake."
    ),
    "P416": (
        "You are not allowed to use COD payment for delivery to home address."
        " Contact support if you believe this is a mistake."
    ),
    "P417": (
        "You are not allowed to use q parameter. It is forbidden for server"
        " partner accounts."
    ),
    "P420": (
        "Parcel not ready for cancel. You can cancel only new, undelivered,"
        " or parcels that are not returned or lost. Make sure parcel is in"
        " transit and try again."
    ),
    "P421": (
        "Invalid parcel weight. Make sure you are sending value between 0"
        " and 10^6."
    ),
    "P422": (
        "Address not found. Try to call just with postal code and country."
    ),
    "P423": "Nearby locker not found.",
    "P424": (
        "Invalid region format. Please ensure the format includes a language"
        " code followed by a country code in ISO 3166-1 alpha-2 format,"
        " separated by a hyphen, e.g. el-GR, or region exists in context."
    ),
    "P430": (
        "Parcel not ready for AnyAPM confirmation. Parcel is probably already"
        " confirmed or being delivered. Contact support if you believe this is"
        " a mistake."
    ),
    "P440": (
        "Ambiguous partner. Your account is linked to multiple partners and it"
        " is unclear on whose behalf you want to perform this action. Send"
        " X-PartnerID header with ID of the partner you want to manage. You"
        " can get list of available Partner IDs from /entrusted-partners"
        " endpoint."
    ),
    "P441": (
        "Invalid X-PartnerID header. Value you provided for X-PartnerID header"
        " is either invalid or references partner you don't have access to."
        " Make sure you are sending ID from /entrusted-partners endpoint."
    ),
    "P442": (
        "Invalid limit query parameter. The query limit for this API has been"
        " exceeded. Please reduce the size of your query (max allowed is 100)."
    ),
}


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class BoxNowError(Exception):
    """Base class for all BoxNow-related errors."""


class BoxNowConfigError(BoxNowError):
    """Raised when required BoxNow credentials or settings are missing."""


class BoxNowAPIError(BoxNowError):
    """
    Raised for non-2xx HTTP responses from the BoxNow API.

    Attributes:
        status_code: HTTP status code returned by BoxNow.
        code:        BoxNow application error code (e.g. "P410"), or None.
        message:     Human-readable error message from BoxNow.
        details:     Additional structured error detail dict, or None.
        response_text: Raw response body text for debugging.
    """

    def __init__(
        self,
        status_code: int,
        code: str | None = None,
        message: str = "",
        details: dict | None = None,
        *,
        response_text: str = "",
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.response_text = response_text
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"BoxNow API {self.status_code} [{self.code}]: {self.message}"


class BoxNowAuthError(BoxNowAPIError):
    """
    Raised for HTTP 401 / 403 responses from BoxNow.

    Indicates that the access token is expired, invalid, or the account
    has been disabled.
    """


class BoxNowRetryableError(BoxNowAPIError):
    """
    Raised for transient failures that Celery tasks should auto-retry on.

    Covers HTTP 5xx responses and connection-level errors (wrapped in this
    class so Celery's ``autoretry_for`` can target a single type).
    """
