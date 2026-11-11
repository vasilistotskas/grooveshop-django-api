"""Runtime config for myDATA: ``extra_settings`` knobs plus the tenant's
own credentials (``tenant.credentials.mydata_credentials``).

Keeps config access in one place so the rest of the package can ask
"is myDATA ready?" without sprinkling ``Setting.get(...)`` calls
everywhere. Mutable at runtime — operators flip ``MYDATA_ENABLED`` via
the Django admin and a store edits its credentials on its tenant row; no
code deploy needed.

Retail receipts are issued through a fiscal device (ΦΗΜ) or a licensed
provider (ν.4308/2014 art. 12 §8, §10). The ERP channel this package
implements is only for a business exempt from the ΦΗΜ, so a store that
takes real orders and has not recorded that exemption
(``MYDATA_FHM_EXEMPTION_GROUND``) is never ready. AADE's sandbox (``dev``)
registers test documents only ("για τη φάση της ανάπτυξης και διενέργειας
δοκιμών", myDATA REST API documentation), so a store that takes no real
orders — a demo store, or any store outside the production deployment —
may issue to the sandbox without one.

The dev/prod base URLs are **hard-coded constants** because they are
published AADE endpoints, not customer-supplied. Accidentally pointing
prod code at a user-set URL would be a bug factory. An environment value
that is neither is refused (the store is not ready), never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.conf import settings

from core.store_settings import store_setting
from tenant.credentials import mydata_credentials
from tenant.membership import is_demo_store

type Environment = Literal["dev", "prod"]

_BASE_URLS: dict[Environment, str] = {
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
    fhm_exemption_ground: str
    #: The store takes real orders: a store not flagged ``is_demo``, on
    #: the production deployment (``settings.REAL_ORDERS_DEPLOYMENT``).
    takes_real_orders: bool
    auto_submit: bool
    #: ``None`` when ``MYDATA_ENVIRONMENT`` is neither ``dev`` nor ``prod``.
    environment: Environment | None
    user_id: str
    subscription_key: str
    invoice_series_prefix: str
    credit_series_prefix: str
    issuer_branch: int
    request_timeout_seconds: int

    @property
    def base_url(self) -> str:
        """Return the AADE endpoint base URL for this environment."""
        if self.environment is None:
            raise ValueError("MYDATA_ENVIRONMENT is neither 'dev' nor 'prod'")
        return _BASE_URLS[self.environment]

    @property
    def issues_to_sandbox_only(self) -> bool:
        """The documents go to AADE's sandbox, from a store that takes no
        real orders: test registrations, never a tax document a customer
        relies on, so no ΦΗΜ exemption is needed for them."""
        return self.environment == "dev" and not self.takes_real_orders

    def is_ready(self) -> bool:
        """True when the platform issues this store's retail documents:
        myDATA is enabled with a known environment, the store has
        recorded the legal ground of its ΦΗΜ exemption (or only issues to
        the sandbox), and its credentials are populated. The call sites
        should no-op rather than raise when False — the store then issues
        its documents outside the platform."""
        return bool(
            self.enabled
            and self.environment is not None
            and (self.fhm_exemption_ground or self.issues_to_sandbox_only)
            and self.user_id
            and self.subscription_key
        )

    def issues_automatically(self) -> bool:
        """True when the platform issues its retail documents by itself:
        the receipt when an order ships, a credit document when money goes
        back. ``MYDATA_AUTO_SUBMIT`` off leaves issuing to the staff
        actions on the order, which work whenever :meth:`is_ready`."""
        return self.is_ready() and self.auto_submit

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


def _environment(value: object) -> Environment | None:
    if value == "dev":
        return "dev"
    if value == "prod":
        return "prod"
    return None


def load_config() -> MyDataConfig:
    """Build a :class:`MyDataConfig` from the current ``extra_settings``
    values and the active tenant's credentials. Call per task
    invocation — not cached."""
    credentials = mydata_credentials()
    return MyDataConfig(
        enabled=bool(store_setting("MYDATA_ENABLED")),
        fhm_exemption_ground=str(
            store_setting("MYDATA_FHM_EXEMPTION_GROUND")
        ).strip(),
        takes_real_orders=settings.REAL_ORDERS_DEPLOYMENT
        and not is_demo_store(),
        auto_submit=bool(store_setting("MYDATA_AUTO_SUBMIT")),
        environment=_environment(store_setting("MYDATA_ENVIRONMENT")),
        user_id=credentials["user_id"],
        subscription_key=credentials["subscription_key"],
        invoice_series_prefix=str(
            store_setting("MYDATA_INVOICE_SERIES_PREFIX") or "GRVP"
        ),
        credit_series_prefix=str(
            store_setting("MYDATA_CREDIT_SERIES_PREFIX") or "GRVC"
        ),
        issuer_branch=int(store_setting("MYDATA_ISSUER_BRANCH") or 0),
        request_timeout_seconds=int(
            store_setting("MYDATA_REQUEST_TIMEOUT_SECONDS") or 30
        ),
    )
