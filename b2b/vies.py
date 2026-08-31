"""EU VIES VAT-number verification client.

Same outbound-HTTP posture as ``order/mydata/client.py``: a shared
``requests.Session``, an explicit short timeout, and one typed
"service unavailable" exception so callers can degrade gracefully —
VIES has scheduled member-state downtime, and an outage must never
block a business-profile submission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

VIES_CHECK_URL = (
    "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
)
DEFAULT_TIMEOUT_SECONDS = 5.0


class ViesUnavailableError(Exception):
    """VIES could not answer (network error, 5xx, malformed body)."""


@dataclass(frozen=True)
class ViesResult:
    valid: bool
    name: str
    address: str


class ViesClient:
    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def check_vat(self, country_code: str, vat_number: str) -> ViesResult:
        try:
            response = self._session.post(
                VIES_CHECK_URL,
                json={
                    "countryCode": country_code,
                    "vatNumber": vat_number,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ViesUnavailableError(str(exc)) from exc

        if "valid" not in payload:
            # VIES signals faults inside a JSON body shaped
            # ``{actionSucceed: false, errorWrappers: [{error, message}]}``
            # (per the official swagger_publicVAT.yaml) — e.g.
            # MS_UNAVAILABLE / MS_MAX_CONCURRENT_REQ / SERVICE_UNAVAILABLE.
            # Anything without a verdict is UNAVAILABLE, never "invalid".
            errors = payload.get("errorWrappers") or []
            detail = (
                "; ".join(
                    str(entry.get("error") or entry.get("message") or "")
                    for entry in errors
                    if isinstance(entry, dict)
                ).strip("; ")
                or "no verdict in response"
            )
            raise ViesUnavailableError(detail)

        return ViesResult(
            valid=bool(payload["valid"]),
            name=(payload.get("name") or "").strip(),
            address=" ".join((payload.get("address") or "").split()),
        )
