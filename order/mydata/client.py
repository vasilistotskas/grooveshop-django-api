"""HTTP client for the AADE myDATA REST API.

Thin wrapper around :mod:`requests`. The retry policy is NOT in this
client — it's the caller's job (``order.mydata.service`` /
Celery task) to classify exceptions and retry, because retries need
to persist state (``mydata_status``, ``mydata_error_*``) between
attempts which the client shouldn't know about.

What this layer does handle:

- Timeouts (default 30s, configurable via ``MYDATA_REQUEST_TIMEOUT_SECONDS``).
- Header injection from :class:`MyDataConfig`.
- Exception taxonomy — turn ``requests`` failures into our typed
  :class:`MyDataError` subclasses so callers don't import ``requests``.
- Logging every request/response (redacted) so ops can debug
  without the Celery task dumping bodies.
"""

from __future__ import annotations

import logging

import requests

from order.mydata.config import MyDataConfig
from order.mydata.exceptions import (
    MyDataAuthError,
    MyDataTransportError,
)

logger = logging.getLogger(__name__)


class MyDataClient:
    """Stateful HTTP client bound to a :class:`MyDataConfig`.

    Construct fresh per task invocation (config snapshot may change
    between calls; sessions are cheap).
    """

    def __init__(self, config: MyDataConfig):
        self._config = config
        self._session = requests.Session()
        self._session.headers.update(config.headers())

    def send_invoices(self, xml_bytes: bytes) -> bytes:
        """POST the ``InvoicesDoc`` payload to ``/SendInvoices``.

        Returns the raw response XML bytes — caller parses via
        :func:`order.mydata.parser.parse_response_doc`.
        """
        return self._post("/SendInvoices", xml_bytes)

    def cancel_invoice(
        self, mark: int, entity_vat_number: str | None = None
    ) -> bytes:
        """POST ``/CancelInvoice?mark={mark}``. Body is empty — all
        params travel on the query string per AADE v1.0.10 §4.2.5."""
        params: dict[str, str] = {"mark": str(mark)}
        if entity_vat_number:
            params["entityVatNumber"] = entity_vat_number
        url = f"{self._config.base_url}/CancelInvoice"
        logger.info(
            "myDATA CancelInvoice mark=%s env=%s",
            mark,
            self._config.environment,
        )
        try:
            response = self._session.post(
                url,
                params=params,
                timeout=self._config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("myDATA CancelInvoice transport error: %s", exc)
            raise MyDataTransportError(str(exc)) from exc
        self._raise_for_status(response)
        return response.content

    def _post(self, path: str, xml_bytes: bytes) -> bytes:
        url = f"{self._config.base_url}{path}"
        logger.info(
            "myDATA POST %s bytes=%d env=%s",
            path,
            len(xml_bytes),
            self._config.environment,
        )
        try:
            response = self._session.post(
                url,
                data=xml_bytes,
                timeout=self._config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("myDATA %s transport error: %s", path, exc)
            raise MyDataTransportError(str(exc)) from exc
        self._raise_for_status(response)
        return response.content

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        """Translate raw HTTP errors to our exception hierarchy.

        Row-level ``ValidationError`` codes come back inside a 200
        response body — the service layer decides those. Here we only
        classify transport-level failures.
        """
        if 200 <= response.status_code < 300:
            return
        body_preview = (response.text or "")[:500]
        if response.status_code in (401, 403):
            raise MyDataAuthError(
                f"AADE rejected credentials ({response.status_code}): "
                f"{body_preview}",
                code=str(response.status_code),
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise MyDataTransportError(
                f"AADE HTTP {response.status_code}: {body_preview}",
                code=str(response.status_code),
            )
        # 4xx other than auth / throttle → payload problem. Fall back
        # to a transport error so the caller retries once; the service
        # layer converts terminal row-level errors to ValidationError.
        raise MyDataTransportError(
            f"AADE HTTP {response.status_code}: {body_preview}",
            code=str(response.status_code),
        )
