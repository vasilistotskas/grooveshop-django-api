class MetaCapiError(Exception):
    """Base error for Meta Conversions API failures."""


class MetaCapiTransientError(MetaCapiError):
    """Retryable failure (network blip, 5xx, rate limit).

    Celery's ``autoretry_for`` only retries this subclass — permanent
    errors (bad pixel ID, 400 schema violation) are surfaced as
    ``MetaCapiError`` and logged once instead of looping.
    """


class MetaCapiConfigError(MetaCapiError):
    """Configuration is missing or invalid (no pixel ID, no token).

    Raised before any HTTP work so an operator sees the misconfig
    in logs instead of an opaque 401 from Meta.
    """
