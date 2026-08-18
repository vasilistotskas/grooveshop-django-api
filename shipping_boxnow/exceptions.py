from __future__ import annotations

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
