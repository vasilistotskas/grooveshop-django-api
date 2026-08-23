"""Thin facade over ``facebook_business`` SDK.

Why a facade and not direct SDK usage everywhere?
* Centralises the API init (token + version + partner_agent) so a
  config drift in one call site can't escape into events.
* Translates raw SDK exceptions into our typed
  ``MetaCapiTransientError`` / ``MetaCapiError`` split so the Celery
  task's ``autoretry_for`` works correctly.
* Keeps test seams tidy: tests stub out ``MetaCapiClient.send`` and
  never touch the real SDK or HTTP layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any

from django.conf import settings

from meta_capi.exceptions import (
    MetaCapiConfigError,
    MetaCapiError,
    MetaCapiTransientError,
)
from tenant.credentials import (
    tenant_meta_capi_access_token,
    tenant_meta_pixel_id,
)

if TYPE_CHECKING:
    from facebook_business.adobjects.serverside.event import Event

logger = logging.getLogger(__name__)

# A per-tenant ``FacebookAdsApi`` instance keyed by (token, version).
#
# CRITICAL: these are STANDALONE api instances, never the SDK's
# process-global default. ``FacebookAdsApi.init()`` calls
# ``set_default_api()``, and one Celery worker serves every tenant — so
# the default-api singleton is a cross-tenant hazard: after tenant B's
# dispatch initialises it, a cache-hit dispatch for tenant A that skips
# re-init would post A's events with B's token (which Meta rejects on a
# pixel/token mismatch, silently losing A's conversions — or worse, if B
# has access to A's pixel, mislabels them). We construct the api
# directly and hand it to the pixel call explicitly (see ``send``), so
# no dispatch ever depends on which tenant last touched the global.
#
# The lock guards only the cold-cache construction window (the Celery
# pool may re-enter ``send`` concurrently for the same token).
_api_cache: dict[tuple[str, str], Any] = {}
_api_cache_lock = Lock()


def _get_api(access_token: str, api_version: str) -> Any:
    cache_key = (access_token, api_version)
    cached = _api_cache.get(cache_key)
    if cached is not None:
        return cached
    with _api_cache_lock:
        cached = _api_cache.get(cache_key)
        if cached is not None:
            return cached
        from facebook_business.api import FacebookAdsApi  # noqa: PLC0415
        from facebook_business.session import (  # noqa: PLC0415
            FacebookSession,
        )

        # Direct construction — NOT FacebookAdsApi.init(), which would
        # also stamp this token onto the process-global default.
        session = FacebookSession(access_token=access_token)
        api = FacebookAdsApi(session, api_version=api_version)
        _api_cache[cache_key] = api
        return api


@dataclass(frozen=True)
class MetaCapiResponse:
    events_received: int
    fbtrace_id: str
    messages: list[str]


class MetaCapiClient:
    """Server-side dispatcher for Meta Conversions API events."""

    def __init__(
        self,
        *,
        pixel_id: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
        test_event_code: str | None = None,
        partner_agent: str | None = None,
    ) -> None:
        # Tenant-only credentials — no platform-wide fallback (an
        # unconfigured tenant means CAPI is disabled for it, not
        # "borrow the platform's token"). Explicit constructor args
        # (used in tests and admin actions) still win.
        self.pixel_id = pixel_id or tenant_meta_pixel_id()
        self.access_token = access_token or tenant_meta_capi_access_token()
        self.api_version = api_version or settings.META_CAPI_API_VERSION
        # Empty string means "no test code" — production posture.
        self.test_event_code = (
            test_event_code
            if test_event_code is not None
            else settings.META_CAPI_TEST_EVENT_CODE
        ) or None
        self.partner_agent = partner_agent or settings.META_CAPI_PARTNER_AGENT

    def _ensure_configured(self) -> None:
        if not self.pixel_id:
            raise MetaCapiConfigError(
                "Tenant.meta_pixel_id / meta_capi_dataset_id is not set"
            )
        if not self.access_token:
            raise MetaCapiConfigError(
                "Tenant.meta_capi_access_token is not set"
            )

    def send(self, events: list[Event]) -> MetaCapiResponse:
        """Dispatch ``events`` to Meta in a single batched POST.

        Raises:
            MetaCapiConfigError: pixel ID / token missing
            MetaCapiTransientError: 5xx, network failure, rate limit
            MetaCapiError: 4xx schema reject (non-retryable)
        """
        if not events:
            return MetaCapiResponse(
                events_received=0, fbtrace_id="", messages=[]
            )

        self._ensure_configured()

        # Lazy imports — keep module import cheap (no SDK side effects
        # at Django boot) and the SDK out of the unit-test fast path.
        from facebook_business.adobjects.adspixel import (  # noqa: PLC0415
            AdsPixel,
        )
        from facebook_business.adobjects.serverside.event_request import (
            EventRequest,
        )
        from facebook_business.exceptions import FacebookRequestError

        # Resolve THIS tenant's own api instance (see ``_get_api``).
        api = _get_api(self.access_token, self.api_version)

        # ``EventRequest`` is reused only to serialise the payload.
        # ``request.execute()`` is deliberately NOT called: it issues the
        # POST through ``AdsPixel(pixel_id)`` with no api argument, which
        # falls back to the global default api — the exact cross-tenant
        # bleed ``_get_api`` exists to prevent. Instead we drive the same
        # pixel edge with this tenant's api pinned explicitly.
        request = EventRequest(
            events=events,
            test_event_code=self.test_event_code,
            partner_agent=self.partner_agent,
            pixel_id=self.pixel_id,
        )
        params = request.get_params()

        try:
            response = AdsPixel(self.pixel_id, api=api).create_event(
                fields=[], params=params
            )
        except FacebookRequestError as exc:
            status = getattr(exc, "http_status", None) or 0
            body = getattr(exc, "body", None) or {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            fbtrace_id = err.get("fbtrace_id", "")
            message = (
                err.get("error_user_msg") or err.get("message") or str(exc)
            )

            # 4xx are usually our fault (bad pixel id, bad payload).
            # 5xx and rate-limit (429) are Meta's fault; retry with
            # exponential backoff via Celery.
            if 500 <= status < 600 or status in (408, 429):
                raise MetaCapiTransientError(
                    f"Meta CAPI {status}: {message} (fbtrace={fbtrace_id})"
                ) from exc
            raise MetaCapiError(
                f"Meta CAPI {status}: {message} (fbtrace={fbtrace_id})"
            ) from exc
        except Exception as exc:  # pragma: no cover — defensive
            # SDK can also raise ConnectionError / Timeout from
            # underlying requests session; treat as transient.
            raise MetaCapiTransientError(str(exc)) from exc

        # ``create_event`` returns the raw pixel-events response (an
        # ``AbstractCrudObject``, dict-like) rather than the SDK's
        # ``EventResponse`` wrapper that ``request.execute()`` built —
        # read the CAPI response fields the same way ``execute()`` does.
        return MetaCapiResponse(
            events_received=int(response.get("events_received", 0) or 0),
            fbtrace_id=str(response.get("fbtrace_id", "") or ""),
            messages=list(response.get("messages", []) or []),
        )
