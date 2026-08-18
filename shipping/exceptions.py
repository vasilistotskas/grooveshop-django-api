from __future__ import annotations


class ShippingError(Exception):
    """Base class for shipping-abstraction errors."""


class ShippingProviderNotFoundError(ShippingError):
    """Raised when a provider code is not registered in the carrier registry."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            f"Shipping provider '{code}' is not registered. "
            "Make sure its app is in INSTALLED_APPS and AppConfig.ready() "
            "calls register_provider()."
        )
