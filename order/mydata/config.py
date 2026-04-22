"""Runtime config for myDATA, resolved from ``extra_settings``.

Keeps config access in one place so the rest of the package can ask
"is myDATA ready?" without sprinkling ``Setting.get(...)`` calls
everywhere. Mutable at runtime — operators flip ``MYDATA_ENABLED`` and
credentials via the Django admin; no code deploy needed.

The dev/prod base URLs are **hard-coded constants** because they are
published AADE endpoints, not customer-supplied. Accidentally pointing
prod code at a user-set URL would be a bug factory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from extra_settings.models import Setting

_BASE_URLS: dict[str, str] = {
    # Published by AADE: https://www.aade.gr/en/mydata
    "dev": "https://mydataapidev.aade.gr",
    "prod": "https://mydatapi.aade.gr/myDATA",
}


@dataclass(frozen=True)
class MyDataConfig:
    """Immutable snapshot of the myDATA runtime config.

    Resolved once per task invocation so config changes mid-task
    don't produce a half-consistent submission. ``is_ready()`` is the
    single gate every caller should check before making an HTTP call.
    """

    enabled: bool
    auto_submit: bool
    environment: Literal["dev", "prod"]
    user_id: str
    subscription_key: str
    invoice_series_prefix: str
    issuer_branch: int
    request_timeout_seconds: int

    @property
    def base_url(self) -> str:
        """Return the AADE endpoint base URL for this environment."""
        return _BASE_URLS[self.environment]

    def is_ready(self) -> bool:
        """True when myDATA is enabled and all required credentials
        are populated. The call sites should no-op rather than raise
        when False — the integration is opt-in, not mandatory."""
        return bool(self.enabled and self.user_id and self.subscription_key)

    def headers(self) -> dict[str, str]:
        """Return the authentication headers AADE expects on every
        request. Kept as a method (not a property) because
        :mod:`requests` mutates header dicts by accident elsewhere."""
        # Header names lowercased to match AADE v1.0.10 §4.1.2 spec
        # exactly. HTTP headers are case-insensitive per RFC 7230, but
        # matching the spec avoids surprises if any proxy gets pedantic.
        return {
            "aade-user-id": self.user_id,
            "ocp-apim-subscription-key": self.subscription_key,
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/xml",
        }


def load_config() -> MyDataConfig:
    """Build a :class:`MyDataConfig` from current ``extra_settings``
    values. Call per task invocation — not cached."""
    env = Setting.get("MYDATA_ENVIRONMENT", default="dev")
    # Defend against typos in the admin — anything we don't recognise
    # falls back to ``dev`` so a misconfigured prod flag can't hit the
    # wrong base URL.
    if env not in _BASE_URLS:
        env = "dev"
    return MyDataConfig(
        enabled=bool(Setting.get("MYDATA_ENABLED", default=False)),
        auto_submit=bool(Setting.get("MYDATA_AUTO_SUBMIT", default=False)),
        environment=env,  # type: ignore[arg-type]
        user_id=str(Setting.get("MYDATA_USER_ID", default="") or ""),
        subscription_key=str(
            Setting.get("MYDATA_SUBSCRIPTION_KEY", default="") or ""
        ),
        invoice_series_prefix=str(
            Setting.get("MYDATA_INVOICE_SERIES_PREFIX", default="GRVP")
            or "GRVP"
        ),
        issuer_branch=int(Setting.get("MYDATA_ISSUER_BRANCH", default=0) or 0),
        request_timeout_seconds=int(
            Setting.get("MYDATA_REQUEST_TIMEOUT_SECONDS", default=30) or 30
        ),
    )
