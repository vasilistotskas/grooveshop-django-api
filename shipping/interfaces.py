"""Carrier-adapter interface and in-memory registry.

A "carrier" is a courier integration (BoxNow, ACS, ELTA, ...).  Each
provider app implements this interface once and registers its adapter
class in ``AppConfig.ready()``:

    @register_provider
    class AcsCarrier(ShippingCarrierInterface):
        code = "acs"
        ...

Higher layers (``ShippingService``, the order-flow code, the
``OrderDetailSerializer``) never import provider apps directly — they
look up the adapter via :func:`get_provider`.  This is what makes the
abstraction *dynamic*: adding a new provider is one new app + one
``ShippingProvider`` row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from shipping.exceptions import ShippingProviderNotFoundError

if TYPE_CHECKING:
    from order.models.order import Order

    from shipping.enum import ShippingKind


class ShippingCarrierInterface(ABC):
    """Abstract adapter every shipping provider must implement.

    Methods may be no-ops (e.g. ``fetch_tracking_events`` for a
    webhook-driven provider that already has the events in DB), but
    every method must exist so callers can dispatch unconditionally.
    """

    code: ClassVar[str]

    # Carrier-specific keys that arrive in the create-order request
    # body but are NOT columns on ``Order``. The order-flow code pops
    # these off the request dict before calling ``Order.objects.create``
    # and hands them to ``create_shipment_row`` so each provider reads
    # its own subset. Override per provider — empty tuple means the
    # carrier doesn't add any per-order payload (e.g. flat home-delivery
    # carriers without locker selection).
    payload_keys: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def create_shipment(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Create or fetch the provider's shipment row for ``order``.

        Idempotent: must return the existing shipment when one is already
        attached to the order.
        """

    @abstractmethod
    def cancel_shipment(self, shipment: Any, *, reason: str = "") -> None:
        """Cancel the shipment via the provider's API."""

    @abstractmethod
    def fetch_label_bytes(self, shipment: Any) -> bytes:
        """Return the raw label PDF bytes (provider-format may vary)."""

    @abstractmethod
    def fetch_tracking_events(self, shipment: Any) -> list[dict[str, Any]]:
        """Refresh tracking events from the provider.

        Polling providers (ACS) hit their API; push providers (BoxNow)
        return an empty list because events arrive via webhook.
        """

    @abstractmethod
    def shipment_for_order(self, order: Order) -> Any | None:
        """Return the provider's shipment row attached to ``order`` or None."""

    @abstractmethod
    def serialize_shipment(
        self, shipment: Any, *, context: dict
    ) -> dict | None:
        """Return the provider's detail-serializer dict for ``shipment``."""

    @abstractmethod
    def validate_order_payload(
        self,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
    ) -> dict[str, list[str]]:
        """Return field-level errors for the create-order payload (DRF format).

        Empty dict when the payload is valid for this provider+kind.
        """

    def calculate_shipping_cost(
        self,
        *,
        order_value_amount: float,
        currency: str,
        kind: ShippingKind,
        country_id: str | None = None,
        region_id: str | None = None,
    ) -> tuple[float, str] | None:
        """Return ``(amount, currency)`` for the provider's shipping cost.

        Default: provider has no opinion → caller falls back to the
        global ``CHECKOUT_SHIPPING_PRICE`` / ``FREE_SHIPPING_THRESHOLD``
        Setting rows.  Override when the provider should price its own
        kind (e.g. ACS flat ``ACS_SHIPPING_PRICE``).
        """
        return None

    def is_kind_enabled(self, kind: ShippingKind) -> bool:
        """Return whether the provider's ``kind`` is enabled in checkout.

        Defaults to True so providers that only need the
        ``ShippingProvider.is_active`` master switch don't need to
        override this hook.  Override when a provider has a per-kind
        feature flag — e.g. ACS uses ``ACS_SMARTPOINT_ENABLED`` to gate
        the locker pickup option independently of home delivery.
        """
        return True

    def create_shipment_row(
        self,
        order: Order,
        *,
        kind: ShippingKind,
        payload: dict[str, Any],
        items: list[tuple[Any, int]] | None = None,
    ) -> None:
        """Persist the provider-specific shipment row for ``order``.

        Called from the order-creation paths *synchronously* (the
        Celery task that talks to the courier API runs later, after
        payment succeeds).  Idempotent: a no-op when a shipment row
        already exists.

        Default implementation is a no-op so providers that only
        register for capability advertising (no shipment row) don't
        have to override.  BoxNow + ACS implement this to drop their
        per-Order child rows during checkout.
        """
        return None

    def dispatch_create_shipment_task(self, order: Order) -> None:
        """Enqueue the provider's create-shipment Celery task.

        Called from ``OrderService.handle_payment_succeeded`` after a
        successful payment.  Default is a no-op so providers without
        an asynchronous create step (e.g. local-only fulfilment
        carriers) don't have to override.
        """
        return None


# Module-level adapter registry. Populated by @register_provider at
# AppConfig.ready() time. Lookup via get_provider("acs") etc.
_REGISTRY: dict[str, ShippingCarrierInterface] = {}


def register_provider(
    cls: type[ShippingCarrierInterface],
) -> type[ShippingCarrierInterface]:
    """Class decorator that instantiates and registers a carrier adapter.

    Re-registering the same code overwrites the previous adapter — that
    is intentional so test factories can swap in mock adapters.
    """
    code = cls.code
    if not code:
        raise ValueError(
            f"{cls.__name__} must declare a non-empty class-level 'code' "
            "before @register_provider."
        )
    _REGISTRY[code] = cls()
    return cls


def get_provider(code: str) -> ShippingCarrierInterface:
    """Return the adapter registered under ``code`` or raise."""
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        raise ShippingProviderNotFoundError(code) from exc


def is_registered(code: str) -> bool:
    """Return True when an adapter is registered under ``code``."""
    return code in _REGISTRY


def registered_codes() -> list[str]:
    """Return the list of registered provider codes (for diagnostics)."""
    return sorted(_REGISTRY.keys())


def all_payload_keys() -> tuple[str, ...]:
    """Return the union of every registered carrier's ``payload_keys``.

    Used by the order-creation paths to pop carrier-specific keys off
    the request body before calling ``Order.objects.create`` so adding
    a new carrier doesn't require editing ``order/services.py`` to
    extend a hardcoded ``_SHIPMENT_PAYLOAD_KEYS`` tuple.
    """
    keys: set[str] = set()
    for adapter in _REGISTRY.values():
        keys.update(adapter.payload_keys)
    return tuple(sorted(keys))
