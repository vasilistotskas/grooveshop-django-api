"""Phase 4a tests — ACS dynamic pricing via ACS_Price_Calculation.

Covers:
* Toggle off → flat-rate path unchanged.
* Toggle on + successful API call → live quote returned.
* Toggle on + API failure → graceful fallback to flat rate.
* Cache hit short-circuits the API.
* Free-shipping threshold short-circuits both paths.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    """Quote cache key (acs:price_quote:*) leaks between tests in the
    in-process LocMemCache — clear it so each test owns its state."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def dynamic_pricing_off():
    from extra_settings.models import Setting

    setting, _ = Setting.objects.get_or_create(
        name="ACS_DYNAMIC_PRICING_ENABLED",
        defaults={"value_type": "bool", "value_bool": False},
    )
    setting.value_bool = False
    setting.save(update_fields=["value_bool"])


@pytest.fixture
def dynamic_pricing_on():
    from extra_settings.models import Setting

    setting, _ = Setting.objects.get_or_create(
        name="ACS_DYNAMIC_PRICING_ENABLED",
        defaults={"value_type": "bool", "value_bool": True},
    )
    setting.value_bool = True
    setting.save(update_fields=["value_bool"])


def test_flat_rate_when_toggle_off(dynamic_pricing_off):
    adapter = get_provider("acs")
    quote = adapter.calculate_shipping_cost(
        order_value_amount=10.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert quote is not None
    # Default flat rate from ACS_SHIPPING_PRICE Setting (3.50)
    assert quote == (3.5, "EUR")


def test_free_shipping_short_circuits_dynamic_pricing(dynamic_pricing_on):
    """Even with the live-quote toggle on, the free-shipping
    threshold check fires first so we don't burn an API call when
    the answer is already known to be 0."""
    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        quote = adapter.calculate_shipping_cost(
            order_value_amount=80.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

    assert quote == (0.0, "EUR")
    assert mock_class.called is False


def test_live_quote_used_when_toggle_on(dynamic_pricing_on):
    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {
            "Basic_Ammount": 4.20,
            "Total_Ammount": 5.21,
        }
        quote = adapter.calculate_shipping_cost(
            order_value_amount=10.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

    assert quote == (5.21, "EUR")
    assert instance.price_calculation.called


def test_falls_back_to_flat_rate_on_api_error(dynamic_pricing_on):
    """Transient ACS API failure must never block checkout — the
    flat-rate path is the safety net."""
    from shipping_acs.exceptions import AcsAPIError

    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.side_effect = AcsAPIError(
            alias="ACS_Price_Calculation",
            error_message="Test outage",
        )
        quote = adapter.calculate_shipping_cost(
            order_value_amount=10.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

    assert quote == (3.5, "EUR")  # flat-rate fallback


def test_quote_is_cached_per_country_region(dynamic_pricing_on):
    """The second call with the same (country, region) tuple must
    short-circuit on cache without hitting the API.

    Pins a synthetic in-memory dict in place of ``django.core.cache.cache``
    for the duration of this test so the assertion stays deterministic
    regardless of:
    * sibling-fixture cache mutation between the two body calls;
    * ``transaction.on_commit`` handlers (BoxNow signal cascades
      sometimes clear cache keys eagerly under the test fixture that
      runs ``on_commit`` synchronously);
    * the project's known ``-n auto`` parallelism flakes documented
      in ``project_test_suite_stability.md``.

    Patches ``django.core.cache.cache`` directly because
    ``shipping_acs.carrier`` lazy-imports it inside ``_fetch_live_quote``;
    re-binding the module attribute catches both the first and second
    lookups.
    """
    import uuid
    from unittest.mock import MagicMock

    unique_country = f"T{uuid.uuid4().hex[:6]}"
    adapter = get_provider("acs")

    fake_store: dict[str, object] = {}
    fake_cache = MagicMock()
    fake_cache.get.side_effect = lambda key, default=None: fake_store.get(
        key, default
    )

    def _fake_set(key, value, timeout=None):
        fake_store[key] = value

    fake_cache.set.side_effect = _fake_set

    with (
        patch("django.core.cache.cache", fake_cache),
        patch("shipping_acs.client.AcsClient") as mock_class,
    ):
        instance = mock_class.return_value
        instance.price_calculation.return_value = {"Total_Ammount": 7.50}

        adapter.calculate_shipping_cost(
            order_value_amount=10.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
            country_id=unique_country,
            region_id="A",
        )
        adapter.calculate_shipping_cost(
            order_value_amount=12.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
            country_id=unique_country,
            region_id="A",
        )

    assert instance.price_calculation.call_count == 1
    assert len(fake_store) == 1


def test_invalid_amount_falls_back_to_flat_rate(dynamic_pricing_on):
    """A garbage Total_Ammount value (e.g. None / non-numeric) must
    not propagate — fall back to the flat rate."""
    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {
            "Total_Ammount": "not-a-number"
        }
        quote = adapter.calculate_shipping_cost(
            order_value_amount=10.0,
            currency="EUR",
            kind=ShippingKind.HOME_DELIVERY,
        )

    assert quote == (3.5, "EUR")
