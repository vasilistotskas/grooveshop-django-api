"""HTTP client for the ACS REST API (single endpoint, RPC-style).

The full API is documented in ``docs/acs-web-services.pdf``.  Every
call is a ``POST`` to the same URL with body
``{"ACSAlias": "<MethodName>", "ACSInputParameters": {...}}`` and a
single static ``AcsApiKey`` header.  The wire envelope mis-spells
``ACSOutputResponce`` (sic) — we keep the spelling on the wire layer
and expose typed methods with normal names.

Pagination, OAuth tokens and webhooks do not exist; tracking is
poll-based via ``ACS_Trackingsummary`` / ``ACS_TrackingDetails``.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsConfigError,
    AcsRetryableError,
)

logger = logging.getLogger("shipping_acs.client")

# Mirrors the BoxNow client's retry policy: 3 retries on connection
# errors and 5xx responses with exponential back-off (0.5s, 1s, 2s).
# POSTs are retried because every ACS call is technically a POST and
# the API is idempotent on the operations we use (re-POST of an
# already-issued voucher returns the same Voucher_No back).
_RETRY_CONFIG = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["POST"],
    raise_on_status=False,
)


class AcsClient:
    """Thin RPC wrapper around the ACS REST API.

    One instance per consumer — do not instantiate at import time;
    instantiate inside the call site so missing settings are surfaced
    via :class:`AcsConfigError` only when the client is actually used.

    Credentials default to ``settings.ACS_*`` and can be overridden in
    tests via constructor kwargs.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        company_id: str | None = None,
        company_password: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
        billing_code: str | None = None,
        api_base_url: str | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or getattr(settings, "ACS_API_KEY", "")
        self.company_id = company_id or getattr(settings, "ACS_COMPANY_ID", "")
        self.company_password = company_password or getattr(
            settings, "ACS_COMPANY_PASSWORD", ""
        )
        self.user_id = user_id or getattr(settings, "ACS_USER_ID", "")
        self.user_password = user_password or getattr(
            settings, "ACS_USER_PASSWORD", ""
        )
        self.billing_code = billing_code or getattr(
            settings, "ACS_BILLING_CODE", ""
        )
        self.api_base_url = (
            api_base_url
            or getattr(
                settings,
                "ACS_API_BASE_URL",
                "https://webservices.acscourier.net/ACSRestServices/api/ACSAutoRest",
            )
        ).rstrip("/")
        self.timeout = timeout or getattr(settings, "ACS_HTTP_TIMEOUT", 15)

        missing = [
            name
            for name, value in [
                ("ACS_API_KEY", self.api_key),
                ("ACS_COMPANY_ID", self.company_id),
                ("ACS_COMPANY_PASSWORD", self.company_password),
                ("ACS_USER_ID", self.user_id),
                ("ACS_USER_PASSWORD", self.user_password),
            ]
            if not value
        ]
        if missing:
            raise AcsConfigError(
                f"Missing required ACS settings: {', '.join(missing)}"
            )

        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            adapter = HTTPAdapter(max_retries=_RETRY_CONFIG)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Internal call helper
    # ------------------------------------------------------------------

    def _creds(self) -> dict[str, Any]:
        """Return the per-request credential block injected into every body."""
        return {
            "Company_ID": self.company_id,
            "Company_Password": self.company_password,
            "User_ID": self.user_id,
            "User_Password": self.user_password,
        }

    def _call(
        self,
        alias: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an ACS RPC call.

        Returns the unwrapped ``ACSOutputResponce`` payload (note the
        misspelling — keep it on the wire side).  Raises typed
        exceptions on transport or business errors.
        """
        body = {
            "ACSAlias": alias,
            "ACSInputParameters": {**self._creds(), **(params or {})},
        }

        try:
            response = self._session.post(
                self.api_base_url,
                json=body,
                headers={
                    "AcsApiKey": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.ConnectionError as exc:
            raise AcsRetryableError(
                alias=alias,
                error_message=f"ACS connection error: {exc}",
            ) from exc

        # Auth failures bypass the JSON envelope.
        if response.status_code in (403, 406):
            raise AcsAuthError(
                alias=alias,
                error_message=(
                    "ACS rejected the request — invalid AcsApiKey or "
                    "the calling IP is not in the allow-list."
                ),
                http_status=response.status_code,
            )

        if response.status_code >= 500:
            logger.warning(
                "ACS %s → HTTP %s: %s",
                alias,
                response.status_code,
                response.text[:500],
            )
            raise AcsRetryableError(
                alias=alias,
                error_message=f"ACS server error {response.status_code}",
                http_status=response.status_code,
            )

        if response.status_code >= 400:
            raise AcsAPIError(
                alias=alias,
                error_message=response.text[:500] or "ACS HTTP error",
                http_status=response.status_code,
            )

        try:
            payload = response.json() or {}
        except ValueError as exc:
            raise AcsAPIError(
                alias=alias,
                error_message=f"ACS returned non-JSON body: {exc}",
                http_status=response.status_code,
            ) from exc

        if payload.get("ACSExecution_HasError"):
            error_message = payload.get("ACSExecutionErrorMessage") or (
                "ACS reported an unspecified error."
            )
            raise AcsAPIError(
                alias=alias,
                error_message=error_message,
                http_status=response.status_code,
                raw=payload,
            )

        # The wire envelope mis-spells ACSOutputResponce; we accept both
        # spellings defensively in case ACS ever fixes it.
        return (
            payload.get("ACSOutputResponce")
            or payload.get("ACSOutputResponse")
            or {}
        )

    # ------------------------------------------------------------------
    # Helpers for unwrapping common response shapes
    # ------------------------------------------------------------------

    @staticmethod
    def _value_output(envelope: dict[str, Any]) -> dict[str, Any]:
        """Return the first ACSValueOutput row, or {} when missing."""
        rows = envelope.get("ACSValueOutput") or []
        return rows[0] if rows else {}

    @staticmethod
    def _table_output(envelope: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the ACSTableOutput.Table_Data rows, or [] when missing."""
        return (envelope.get("ACSTableOutput") or {}).get("Table_Data") or []

    # ------------------------------------------------------------------
    # Voucher creation / management
    # ------------------------------------------------------------------

    def create_voucher(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call ``ACS_Create_Voucher``.

        ``params`` must include billing_code, recipient address fields,
        weight, item_quantity, charge_type, and optionally
        ``Acs_Station_Destination`` / ``Acs_Station_Branch_Destination``
        for Smartpoint pickups.

        Returns the first ``ACSValueOutput`` row, which on success
        contains ``Voucher_No`` (and ``Voucher_No_Return`` when
        ``With_Return_Voucher`` was requested).
        """
        envelope = self._call("ACS_Create_Voucher", params)
        return self._value_output(envelope)

    def get_multipart_vouchers(self, main_voucher_no: str) -> list[str]:
        """Return per-item child voucher numbers for ``main_voucher_no``.

        Used when ``Item_Quantity > 1``: ACS only mints child vouchers
        the first time you call this method (per PDF section "ΠΟΛΛΑΠΛΑ
        VOUCHERS").
        """
        envelope = self._call(
            "ACS_Get_Multipart_Vouchers",
            {"Main_Voucher_No": main_voucher_no},
        )
        rows = self._table_output(envelope)
        return [
            row["MultiPart_Voucher_No"]
            for row in rows
            if row.get("MultiPart_Voucher_No")
        ]

    def delete_voucher(self, voucher_no: str) -> None:
        """Cancel a voucher via ``ACS_Delete_Voucher``.

        Per PDF: only valid before the voucher is included in a pickup
        list.  Caller (``AcsService.cancel_voucher``) enforces this.
        """
        self._call("ACS_Delete_Voucher", {"Voucher_No": voucher_no})

    def print_voucher(
        self,
        voucher_no: str,
        *,
        print_type: int = 2,
        start_position: int = 1,
    ) -> bytes:
        """Return the voucher label PDF bytes via ``ACS_Print_Voucher``.

        Per PDF: response is a base64-encoded PDF in
        ``ACSObjectOutput`` keyed by voucher_no.  print_type=2 → laser
        layout (4 vouchers per A4); print_type=1 → roll printer
        (single voucher).
        """
        envelope = self._call(
            "ACS_Print_Voucher",
            {
                "Voucher_No": voucher_no,
                "Print_Type": print_type,
                "Start_Position": start_position,
            },
        )
        return _decode_pdf(envelope, key=voucher_no, alias="ACS_Print_Voucher")

    # ------------------------------------------------------------------
    # Pickup list (daily manifest)
    # ------------------------------------------------------------------

    def issue_pickup_list(self, *, pickup_date: str) -> dict[str, Any]:
        """Finalise the day's vouchers and return the pickup list info.

        ``pickup_date`` must be ``YYYY-MM-DD``.  Returns the first
        ``ACSValueOutput`` row containing ``PickupList_No`` (or an
        ``Error_Message`` when no vouchers are eligible).
        """
        envelope = self._call(
            "ACS_Issue_Pickup_List",
            {"Pickup_Date": pickup_date, "MyData": None},
        )
        return self._value_output(envelope)

    def print_pickup_list(
        self,
        *,
        mass_number: str,
        pickup_date: str,
    ) -> bytes:
        """Return the manifest PDF bytes via ``ACS_Print_Pickup_List``."""
        envelope = self._call(
            "ACS_Print_Pickup_List",
            {"Mass_Number": mass_number, "Pickup_Date": pickup_date},
        )
        return _decode_pdf(
            envelope, key=mass_number, alias="ACS_Print_Pickup_List"
        )

    def get_pickup_lists(self, *, pickup_date: str) -> list[dict[str, Any]]:
        """List all pickup lists for ``pickup_date`` (YYYY-MM-DD)."""
        envelope = self._call(
            "ACS_Get_Pickup_Lists", {"Pickup_Date": pickup_date}
        )
        return self._table_output(envelope)

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def tracking_summary(self, voucher_no: str) -> dict[str, Any]:
        """Return the latest snapshot for ``voucher_no``.

        Maps to the first ``Table_Data`` row of ``ACS_Trackingsummary``,
        or {} when the voucher is unknown.
        """
        envelope = self._call("ACS_Trackingsummary", {"Voucher_No": voucher_no})
        rows = self._table_output(envelope)
        return rows[0] if rows else {}

    def tracking_details(self, voucher_no: str) -> list[dict[str, Any]]:
        """Return the full event history for ``voucher_no``."""
        envelope = self._call("ACS_TrackingDetails", {"Voucher_No": voucher_no})
        return self._table_output(envelope)

    def pod_from_reference_no(self, reference_no: str) -> bytes:
        """Return proof-of-delivery PDF for a partner reference key."""
        envelope = self._call(
            "ACS_POD_FROM_REFERENCE_NO",
            {"reference_no": reference_no, "User_locals": "GR"},
        )
        return _decode_pdf(
            envelope, key=reference_no, alias="ACS_POD_FROM_REFERENCE_NO"
        )

    # ------------------------------------------------------------------
    # Stations / address tools
    # ------------------------------------------------------------------

    def stations(
        self,
        *,
        country: str = "GR",
        shop_kind: int | None = None,
        language: str = "GR",
    ) -> list[dict[str, Any]]:
        """Return ACS shops + Smartpoints, optionally filtered by kind."""
        params: dict[str, Any] = {
            "ACS_SHOP_COUNTRY_ID": country,
            "language": language,
        }
        if shop_kind is not None:
            params["ACS_SHOP_KIND"] = shop_kind
        envelope = self._call("Acs_Stations", params)
        return self._table_output(envelope)

    def address_validation(
        self,
        *,
        address: str,
        address_id: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Validate / geocode an address via ``ACS_Address_Validation``."""
        envelope = self._call(
            "ACS_Address_Validation",
            {
                "Address": address,
                "AddressID": address_id,
                "Language": language,
            },
        )
        rows = envelope.get("ACSValueOutput") or []
        if not rows:
            return {}
        objects = rows[0].get("ACSObjectOutput") or []
        return objects[0] if objects else {}

    def find_station_by_zip(
        self,
        *,
        zip_code: str,
        country: str = "GR",
        language: str = "GR",
    ) -> list[dict[str, Any]]:
        """Resolve a zip to its serving ACS station."""
        envelope = self._call(
            "ACS_Find_Station_By_Zip_Code",
            {
                "Zip_Code": zip_code,
                "Country": country,
                "language": language,
            },
        )
        return self._table_output(envelope)

    def area_find_by_zip_code(
        self,
        *,
        zip_code: str,
        country: str = "GR",
        language: str = "GR",
        show_only_inaccessible: int = 0,
    ) -> list[dict[str, Any]]:
        """List the areas served by a given zip."""
        envelope = self._call(
            "ACS_Area_Find_By_Zip_Code",
            {
                "Zip_Code": zip_code,
                "Country": country,
                "Language": language,
                "Show_Only_Inaccessible_Areas": show_only_inaccessible,
            },
        )
        return self._table_output(envelope)

    def price_calculation(self, params: dict[str, Any]) -> dict[str, Any]:
        """Quote a shipping cost via ``ACS_Price_Calculation``."""
        merged = {"Billing_Code": self.billing_code, **params}
        envelope = self._call("ACS_Price_Calculation", merged)
        return self._value_output(envelope)

    def cod_beneficiary_info(
        self,
        *,
        cod_payment_date: str = "",
        user_locals: str = "GR",
    ) -> list[dict[str, Any]]:
        """List COD payouts owed to us as of ``cod_payment_date`` (YYYY-MM-DD)."""
        envelope = self._call(
            "ACS_COD_Beneficiary_Info",
            {"COD_Payment_Date": cod_payment_date, "User_locals": user_locals},
        )
        return self._table_output(envelope)


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------


def _decode_pdf(envelope: dict[str, Any], *, key: str, alias: str) -> bytes:
    """Decode the base64 PDF blob from an ACS print response.

    ACS observed wire shape (``ACS_Print_Voucher``,
    ``ACS_Print_Pickup_List``, ``ACS_POD_FROM_REFERENCE_NO``):

        ``{"ACSValueOutput": [{"ACSObjectOutput": "<base64>"}, ...]}``

    The PDF docs show a top-level ``ACSObjectOutput`` array keyed by
    voucher number, but the live API actually emits the blob inside
    ``ACSValueOutput[0]['ACSObjectOutput']`` as a single base64
    string. We accept both shapes so a future server-side fix doesn't
    break us, and so the original PDF-based test fixtures keep
    passing.
    """
    candidates: list[str] = []

    value_output = envelope.get("ACSValueOutput") or []
    for entry in value_output:
        if not isinstance(entry, dict):
            continue
        blob = entry.get("ACSObjectOutput")
        if isinstance(blob, str) and blob:
            candidates.append(blob)

    object_output = envelope.get("ACSObjectOutput") or []
    if isinstance(object_output, list):
        for entry in object_output:
            if not isinstance(entry, dict):
                continue
            blob = entry.get(key) or entry.get(str(key))
            if isinstance(blob, str) and blob:
                candidates.append(blob)

    for b64 in candidates:
        try:
            return base64.b64decode(b64)
        except (TypeError, ValueError) as exc:
            raise AcsAPIError(
                alias=alias,
                error_message=(
                    f"ACS returned an undecodable PDF for key {key!r}: {exc}"
                ),
            ) from exc

    raise AcsAPIError(
        alias=alias,
        error_message=f"ACS response contained no PDF blob for key {key!r}.",
    )
