from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowAuthError,
    BoxNowConfigError,
    BoxNowRetryableError,
)

logger = logging.getLogger("shipping_boxnow.client")

# urllib3 Retry configuration shared by all BoxNowClient instances.
# Retries 3 times on connection errors and 5xx responses using exponential
# back-off (0.5 s, 1 s, 2 s).  Only safe / idempotent methods are retried
# automatically; POST retries are handled at the application level via
# BoxNowRetryableError + Celery autoretry.
_RETRY_CONFIG = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET", "POST", "PUT", "DELETE"],
    raise_on_status=False,  # we inspect status ourselves
)


class BoxNowClient:
    """
    Thin HTTP client for the BoxNow Partner API (v1.65 / manual v7.2).

    One instance per consumer — do not instantiate at module import time.
    All credentials default to ``settings.BOXNOW_*``; pass explicit values
    to override (useful in tests).

    OAuth tokens are cached in Django's cache backend (Redis in production)
    under ``boxnow:access_token:{partner_id}`` so that Daphne workers and
    Celery workers share a single token without racing to refresh it.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        partner_id: str | None = None,
        api_base_url: str | None = None,
        location_api_base_url: str | None = None,
        timeout: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.client_id = client_id or getattr(settings, "BOXNOW_CLIENT_ID", "")
        self.client_secret = client_secret or getattr(
            settings, "BOXNOW_CLIENT_SECRET", ""
        )
        self.partner_id = str(
            partner_id or getattr(settings, "BOXNOW_PARTNER_ID", "")
        )
        self.api_base_url = (
            api_base_url
            or getattr(
                settings, "BOXNOW_API_BASE_URL", "https://api-stage.boxnow.gr"
            )
        ).rstrip("/")
        self.location_api_base_url = (
            location_api_base_url
            or getattr(
                settings,
                "BOXNOW_LOCATION_API_BASE_URL",
                "https://locationapi-stage.boxnow.gr",
            )
        ).rstrip("/")
        self.timeout = timeout or getattr(settings, "BOXNOW_HTTP_TIMEOUT", 10)

        missing = [
            name
            for name, value in [
                ("BOXNOW_CLIENT_ID", self.client_id),
                ("BOXNOW_CLIENT_SECRET", self.client_secret),
                ("BOXNOW_PARTNER_ID", self.partner_id),
            ]
            if not value
        ]
        if missing:
            raise BoxNowConfigError(
                f"Missing required BoxNow settings: {', '.join(missing)}"
            )

        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            adapter = HTTPAdapter(max_retries=_RETRY_CONFIG)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

        # Cache key scoped to partner_id so stage and prod credentials stored
        # in the same Redis instance never collide.
        self._token_cache_key = f"boxnow:access_token:{self.partner_id}"

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        """
        Return a valid OAuth bearer token, fetching a new one when necessary.

        The token is cached under ``boxnow:access_token:{partner_id}`` for
        ``expires_in - 60`` seconds so the cache entry expires before the
        real token does (defensive margin).

        Args:
            force_refresh: Bypass the cache and always fetch a fresh token.

        Raises:
            BoxNowAuthError: If BoxNow returns a non-200 response.
        """
        from django.core.cache import cache

        if not force_refresh:
            cached = cache.get(self._token_cache_key)
            if cached:
                return cached

        logger.info(
            "Refreshing BoxNow access token (partner_id=%s)",
            self.partner_id,
        )

        url = f"{self.api_base_url}/api/v1/auth-sessions"
        try:
            response = self._session.post(
                url,
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout,
            )
        except requests.ConnectionError as exc:
            raise BoxNowRetryableError(
                0,
                message=f"Connection error fetching BoxNow token: {exc}",
            ) from exc

        if response.status_code != 200:
            raise BoxNowAuthError(
                status_code=response.status_code,
                message="BoxNow authentication failed",
                response_text=response.text,
            )

        data = self._json(response)
        token: str = data["access_token"]
        expires_in: int = data.get("expires_in", 3600)
        ttl = max(expires_in - 60, 60)

        cache.set(self._token_cache_key, token, timeout=ttl)
        return token

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str | None = None,
        json: Any = None,
        params: dict | None = None,
        headers: dict | None = None,
        _retry_on_401: bool = True,
    ) -> requests.Response:
        """
        Perform an authenticated HTTP request to the BoxNow API.

        Attaches the current Bearer token.  On a 401, refreshes the token
        once and retries.  On 5xx, raises ``BoxNowRetryableError``.  On
        4xx, parses the BoxNow error envelope and raises ``BoxNowAPIError``
        (or ``BoxNowAuthError`` for 401/403).

        Args:
            method:         HTTP verb (GET, POST, PUT, DELETE).
            path:           API path, e.g. ``/api/v1/delivery-requests``.
            base_url:       Override the default ``api_base_url``.  Use
                            ``self.location_api_base_url`` for location calls.
            json:           Request body (serialised to JSON).
            params:         Query-string parameters.
            headers:        Additional HTTP headers (merged, not replaced).
            _retry_on_401:  Internal flag — set False on the retry call to
                            prevent infinite recursion.

        Returns:
            The raw ``requests.Response`` (caller decides how to parse it).

        Raises:
            BoxNowAuthError:      HTTP 401 / 403.
            BoxNowRetryableError: HTTP 5xx or connection error.
            BoxNowAPIError:       Other non-2xx HTTP response.
        """
        effective_base = (base_url or self.api_base_url).rstrip("/")
        url = f"{effective_base}{path}"
        token = self._get_access_token()

        request_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            request_headers.update(headers)

        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                json=json,
                params=params,
                headers=request_headers,
                timeout=self.timeout,
            )
        except requests.ConnectionError as exc:
            raise BoxNowRetryableError(
                0,
                message=f"BoxNow connection error: {exc}",
            ) from exc

        if response.status_code == 401 and _retry_on_401:
            logger.info(
                "BoxNow returned 401; refreshing token and retrying %s %s",
                method.upper(),
                path,
            )
            self._get_access_token(force_refresh=True)
            return self._request(
                method,
                path,
                base_url=base_url,
                json=json,
                params=params,
                headers=headers,
                _retry_on_401=False,
            )

        if response.status_code >= 500:
            logger.warning(
                "BoxNow %s %s → %s: %s",
                method.upper(),
                path,
                response.status_code,
                response.text[:500],
            )
            raise BoxNowRetryableError(
                status_code=response.status_code,
                message=f"BoxNow server error {response.status_code}",
                response_text=response.text,
            )

        if not response.ok:
            logger.warning(
                "BoxNow %s %s → %s: %s",
                method.upper(),
                path,
                response.status_code,
                response.text[:500],
            )
            self._raise_api_error(response)

        return response

    def _raise_api_error(self, response: requests.Response) -> None:
        """Parse the BoxNow error envelope and raise the appropriate error."""
        # Explicit int(...) cast — ``requests``'s type stubs declare
        # ``status_code`` as ``int | None`` so ty would otherwise complain
        # about the int-typed parameter on BoxNowAPIError.
        status_code = int(response.status_code or 0)
        code: str | None = None
        message: str = ""
        details: dict = {}

        try:
            data = response.json()
            # BoxNow error envelope: {"code": "P410", "message": "...", ...}
            code = data.get("code") or data.get("errorCode")
            message = (
                data.get("message") or data.get("error") or response.text[:200]
            )
            details = {
                k: v
                for k, v in data.items()
                if k not in ("code", "errorCode", "message", "error")
            }
        except Exception:
            message = response.text[:200]

        error_cls = (
            BoxNowAuthError if status_code in (401, 403) else BoxNowAPIError
        )
        raise error_cls(
            status_code=status_code,
            code=code,
            message=message,
            details=details or None,
            response_text=response.text,
        )

    def _json(self, response: requests.Response) -> dict:
        """
        Return the parsed JSON body, falling back to ``{}`` for empty bodies.
        """
        if not response.content:
            return {}
        try:
            return response.json()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Locations
    # ------------------------------------------------------------------

    def list_origins(
        self,
        *,
        latlng: str | None = None,
        radius: int | None = None,
        required_size: int | None = None,
        location_type: str | list[str] | None = None,
        name: str | None = None,
    ) -> list[dict]:
        """
        List available pick-up origins (warehouses / depots).

        Uses the dedicated location API base URL for better performance
        (``locationapi-stage.boxnow.gr`` or ``locationapi-production.boxnow.gr``).

        Args:
            latlng:         GPS coordinate string ``"lat,lng"``.  When set,
                            only locations within ``radius`` metres are returned.
            radius:         Search radius in metres (default 25 000).
                            Ignored when ``latlng`` is absent.
            required_size:  Return only locations that can accept a package
                            of the given compartment size (0-3).
            location_type:  Filter by location type(s).  Accepts a single
                            string or a list.  Available values: ``apm``,
                            ``any-apm``, ``warehouse``, ``depot``.
            name:           Return only locations whose name matches.

        Returns:
            List of location dicts from the BoxNow ``data`` array.
        """
        params: dict[str, Any] = {}
        if latlng is not None:
            params["latlng"] = latlng
        if radius is not None:
            params["radius"] = radius
        if required_size is not None:
            params["requiredSize"] = required_size
        if location_type is not None:
            if isinstance(location_type, list):
                params["locationType"] = ",".join(location_type)
            else:
                params["locationType"] = location_type
        if name is not None:
            params["name"] = name

        response = self._request(
            "GET",
            "/api/v1/origins",
            base_url=self.location_api_base_url,
            params=params or None,
        )
        return self._json(response).get("data", [])

    def list_destinations(
        self,
        *,
        latlng: str | None = None,
        radius: int | None = None,
        required_size: int | None = None,
        location_type: str | list[str] | None = None,
        name: str | None = None,
    ) -> list[dict]:
        """
        List available APM (Automatic Parcel Machine) delivery destinations.

        Uses the dedicated location API base URL for better performance.

        Args:
            latlng:         GPS coordinate string ``"lat,lng"``.
            radius:         Search radius in metres (default 25 000).
            required_size:  Compartment size filter (1-3).
            location_type:  Filter by type(s): ``apm``, ``any-apm``,
                            ``warehouse``, ``depot``.
            name:           Name filter.

        Returns:
            List of destination dicts from the BoxNow ``data`` array.
        """
        params: dict[str, Any] = {}
        if latlng is not None:
            params["latlng"] = latlng
        if radius is not None:
            params["radius"] = radius
        if required_size is not None:
            params["requiredSize"] = required_size
        if location_type is not None:
            if isinstance(location_type, list):
                params["locationType"] = ",".join(location_type)
            else:
                params["locationType"] = location_type
        if name is not None:
            params["name"] = name

        response = self._request(
            "GET",
            "/api/v1/destinations",
            base_url=self.location_api_base_url,
            params=params or None,
        )
        return self._json(response).get("data", [])

    # ------------------------------------------------------------------
    # Delivery requests
    # ------------------------------------------------------------------

    def find_closest_locker(
        self,
        *,
        city: str,
        street: str,
        postal_code: str,
        region: str = "el-GR",
        compartment_size: int = 1,
    ) -> dict:
        """
        Find the closest APM locker to a given address.

        Calls ``POST /api/v2/delivery-requests:checkAddressDelivery``.
        The returned ``id`` can be used directly as the
        ``destination.locationId`` in ``create_delivery_request``.

        Args:
            city:             City name.
            street:           Street address.
            postal_code:      Postal code.
            region:           Region/locale string (default ``"el-GR"``).
            compartment_size: Required compartment size 1-3 (default 1).

        Returns:
            Locker dict including ``id``, ``distance``, coordinates, etc.

        Raises:
            BoxNowAPIError: P422 (address not found), P423 (no nearby locker),
                            P424 (invalid region format).
        """
        response = self._request(
            "POST",
            "/api/v2/delivery-requests:checkAddressDelivery",
            json={
                "city": city,
                "street": street,
                "postalCode": postal_code,
                "region": region,
                "compartmentSize": compartment_size,
            },
        )
        return self._json(response)

    def create_delivery_request(self, payload: dict) -> dict:
        """
        Create a delivery request (order a parcel delivery).

        Calls ``POST /api/v1/delivery-requests``.  The caller is responsible
        for building the full ``payload`` dict with camelCase keys matching
        the BoxNow API schema (``orderNumber``, ``paymentMode``,
        ``amountToBeCollected``, ``origin``, ``destination``, ``items``, etc.).

        Args:
            payload: Full delivery-request body (camelCase).

        Returns:
            Response dict ``{"id": "...", "parcels": [{"id": "..."}]}``.

        Raises:
            BoxNowAPIError: e.g. P410 (duplicate order number).
            BoxNowRetryableError: 5xx or connection error.
        """
        response = self._request(
            "POST",
            "/api/v1/delivery-requests",
            json=payload,
        )
        return self._json(response)

    def update_delivery_request(
        self,
        delivery_request_id: str,
        *,
        allow_return: bool,
    ) -> dict:
        """
        Update a delivery request.

        Per the BoxNow API only ``allowReturn`` is mutable after creation.
        Calls ``PUT /api/v1/delivery-requests/{delivery_request_id}``.

        Args:
            delivery_request_id: BoxNow delivery request ID (numeric string).
            allow_return:        New value for the ``allowReturn`` flag.

        Returns:
            Response dict ``{"id": "..."}``.
        """
        response = self._request(
            "PUT",
            f"/api/v1/delivery-requests/{delivery_request_id}",
            json={"allowReturn": allow_return},
        )
        return self._json(response)

    # ------------------------------------------------------------------
    # Parcels
    # ------------------------------------------------------------------

    def cancel_parcel(self, parcel_id: str) -> None:
        """
        Cancel a parcel delivery.

        Calls ``POST /api/v1/parcels/{parcel_id}:cancel``.  Cancelling an
        already-cancelled parcel has no effect (idempotent per BoxNow docs).

        Args:
            parcel_id: BoxNow 10-digit parcel ID / voucher number.

        Raises:
            BoxNowAPIError: P420 if the parcel is not in a cancellable state.
        """
        self._request(
            "POST",
            f"/api/v1/parcels/{parcel_id}:cancel",
        )

    def fetch_parcel_label(
        self,
        parcel_id: str,
        *,
        type: str = "pdf",
        dpi: int | None = None,
    ) -> bytes:
        """
        Fetch the shipping label for a single parcel.

        Calls ``GET /api/v1/parcels/{parcel_id}/label.{type}``.

        Args:
            parcel_id: BoxNow 10-digit parcel ID.
            type:      Label format: ``"pdf"`` (default) or ``"zpl"``.
            dpi:       ZPL printer resolution — ``200`` or ``300``
                       (only relevant for ``type="zpl"``).

        Returns:
            Raw label bytes (PDF binary or ZPL text).
        """
        params: dict[str, Any] = {}
        if dpi is not None:
            params["dpi"] = dpi

        response = self._request(
            "GET",
            f"/api/v1/parcels/{parcel_id}/label.{type}",
            params=params or None,
        )
        return response.content

    def fetch_order_label(
        self,
        order_number: str,
        *,
        type: str = "pdf",
        dpi: int | None = None,
    ) -> bytes:
        """
        Fetch shipping labels for all parcels in a delivery request (order).

        Calls
        ``GET /api/v1/delivery-requests/{order_number}/label.{type}``.

        Args:
            order_number: The order number used when creating the delivery
                          request (``orderNumber`` field).
            type:         ``"pdf"`` (default) or ``"zpl"``.
            dpi:          ZPL DPI (200 or 300).

        Returns:
            Raw label bytes — a single PDF containing all parcel labels.
        """
        params: dict[str, Any] = {}
        if dpi is not None:
            params["dpi"] = dpi

        response = self._request(
            "GET",
            f"/api/v1/delivery-requests/{order_number}/label.{type}",
            params=params or None,
        )
        return response.content

    def get_parcel_info(
        self,
        *,
        parcel_id: str | None = None,
        order_number: str | None = None,
        q: str | None = None,
        payment_state: str | None = None,
        payment_mode: str | None = None,
        state: str | list[str] | None = None,
        page_token: str | None = None,
        limit: int | None = None,
    ) -> dict:
        """
        List / search parcel information.

        Calls ``GET /api/v1/parcels`` with any combination of filters.

        Args:
            parcel_id:     Return only the parcel with this BoxNow ID.
            order_number:  Return only parcels belonging to this order.
            q:             Free-text search across order ID, parcel ID,
                           customer name, email, and phone.
            payment_state: Filter by payment state: ``pending``,
                           ``paid-by-customer``, ``transferred-to-partner``.
            payment_mode:  Filter by payment mode: ``prepaid`` or ``cod``.
            state:         Filter by parcel state(s).  See BoxNow docs for
                           available values (``new``, ``intransit``, etc.).
            page_token:    Pagination cursor returned by a previous response.
            limit:         Page size (default 50, max 100).

        Returns:
            Full response envelope
            ``{"pagination": {...}, "count": int, "data": [...]}``.
        """
        params: dict[str, Any] = {}
        if parcel_id is not None:
            params["parcelId"] = parcel_id
        if order_number is not None:
            params["orderNumber"] = order_number
        if q is not None:
            params["q"] = q
        if payment_state is not None:
            params["paymentState"] = payment_state
        if payment_mode is not None:
            params["paymentMode"] = payment_mode
        if state is not None:
            if isinstance(state, list):
                params["state"] = state  # requests serialises lists correctly
            else:
                params["state"] = state
        if page_token is not None:
            params["pageToken"] = page_token
        if limit is not None:
            params["limit"] = limit

        response = self._request(
            "GET",
            "/api/v1/parcels",
            params=params or None,
        )
        return self._json(response)

    # ------------------------------------------------------------------
    # Delivery partners
    # ------------------------------------------------------------------

    def list_delivery_partners(self) -> list[dict]:
        """
        List available delivery partners.

        Calls ``GET /api/v1/delivery-partners``.

        Returns:
            List of delivery partner dicts from the BoxNow ``data`` array.
        """
        response = self._request("GET", "/api/v1/delivery-partners")
        return self._json(response).get("data", [])
