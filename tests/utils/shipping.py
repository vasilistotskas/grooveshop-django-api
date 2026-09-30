"""Give a test's own country an active ``ShippingRate``.

``CountryFactory`` mints a country with a random alpha-2 code — never
``GR``, the one country the shipping migrations seed a rate for (kept
by ``tests/migration_seed.py``). A checkout test
built around such a country now needs its own rate, or every quote and
every order-creation path rejects it as unavailable
(``ShippingUnavailableError``) — exactly the gate this feature added.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory
from shipping.models import ShippingRate

if TYPE_CHECKING:
    from country.models import Country


def enable_rate(
    country: Country,
    *,
    provider_code: str = "flat_rate",
    kind: str | ShippingKind = ShippingKind.HOME_DELIVERY,
    price: Decimal | float = Decimal("3.00"),
    free_shipping_threshold: Decimal | float | None = Decimal("50.00"),
    max_weight_grams: int | None = None,
    is_active: bool = True,
) -> ShippingRate:
    """Create (or update) the active rate a checkout test needs.

    Reuses the real seeded provider row when ``provider_code`` matches
    one the app ships (``flat_rate``, ``boxnow``, ``acs``) — that row
    already has the right ``supports_home_delivery`` /
    ``supports_pickup_point`` flags and a registered adapter, both of
    which ``ShippingRate.clean()`` and ``ShippingService.active_rate``
    check. Falls back to a fresh ``ShippingProviderFactory`` row for a
    test that wants a made-up provider code.

    ``get_or_create``-like: safe to call once per test country even
    when a previous call (or the autouse GR seed) already created a
    row for the same (provider, country, kind).
    """
    from shipping.models import ShippingProvider

    kind_value = kind.value if isinstance(kind, ShippingKind) else kind

    provider = ShippingProvider.objects.filter(code=provider_code).first()
    if provider is None:
        provider = ShippingProviderFactory.create(
            code=provider_code,
            is_active=True,
            supports_home_delivery=kind_value == ShippingKind.HOME_DELIVERY,
            supports_pickup_point=kind_value == ShippingKind.PICKUP_POINT,
        )
    elif not provider.is_active:
        provider.is_active = True
        provider.save(update_fields=["is_active"])

    rate, _created = ShippingRate.objects.update_or_create(
        provider=provider,
        country=country,
        kind=kind_value,
        defaults={
            "price": price,
            "free_shipping_threshold": free_shipping_threshold,
            "max_weight_grams": max_weight_grams,
            "is_active": is_active,
        },
    )
    return rate
