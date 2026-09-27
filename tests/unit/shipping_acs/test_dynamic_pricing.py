"""Phase 4a tests — ACS dynamic pricing via ACS_Price_Calculation.

``AcsCarrier.live_quote`` is now the sole pricing hook (replaces the
old ``calculate_shipping_cost``): it either returns a live-quoted
``Decimal`` amount, or ``None`` when the toggle is off, the API call
fails, or the station origin is unresolvable — in every ``None`` case
``ShippingService._priced`` falls back to the resolved
``ShippingRate.price`` unchanged, so a transient outage never blocks
checkout. The free-shipping-threshold short-circuit that used to live
here is a ``ShippingRate`` concern now — see
``tests/unit/shipping/test_shipping_service_rates.py``.

Covers:
* Toggle off → ``live_quote`` returns None (caller uses the rate price).
* Toggle on + successful API call → live quote returned.
* Toggle on + API failure → ``None``, not an exception.
* Cache hit short-circuits the API.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache

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
def pin_station_origin():
    """Pin the merchant pickup station for the live-quote path so
    tests don't depend on whether ``ACS_BILLING_CODE`` is set in the
    environment. ``station_origin()`` returns ``None`` in CI without
    a billing code, which would short-circuit ``_fetch_live_quote``
    before the mocked AcsClient is reached and break every quote
    assertion."""
    from shipping_acs import config as acs_config

    with patch.object(acs_config, "station_origin", return_value="ΑΚ"):
        yield


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


def test_live_quote_is_none_when_toggle_off(dynamic_pricing_off):
    adapter = get_provider("acs")
    quote = adapter.live_quote(
        rate=None, country_code="GR", weight_grams=None, currency="EUR"
    )
    assert quote is None


def test_live_quote_used_when_toggle_on(dynamic_pricing_on, pin_station_origin):
    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {
            "Basic_Ammount": 4.20,
            "Total_Ammount": 5.21,
        }
        quote = adapter.live_quote(
            rate=None, country_code="GR", weight_grams=None, currency="EUR"
        )

    assert quote == Decimal("5.21")
    assert instance.price_calculation.called


def test_falls_back_to_none_on_api_error(
    dynamic_pricing_on, pin_station_origin
):
    """Transient ACS API failure must never block checkout — ``None``
    tells ``ShippingService`` to use the resolved rate's own price."""
    from shipping_acs.exceptions import AcsAPIError

    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.side_effect = AcsAPIError(
            alias="ACS_Price_Calculation",
            error_message="Test outage",
        )
        quote = adapter.live_quote(
            rate=None, country_code="GR", weight_grams=None, currency="EUR"
        )

    assert quote is None


def test_quote_is_cached_per_country_region(
    dynamic_pricing_on, pin_station_origin
):
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

        adapter.live_quote(
            rate=None,
            country_code=unique_country,
            region_id="A",
            weight_grams=None,
            currency="EUR",
        )
        adapter.live_quote(
            rate=None,
            country_code=unique_country,
            region_id="A",
            weight_grams=None,
            currency="EUR",
        )

    assert instance.price_calculation.call_count == 1
    assert len(fake_store) == 1


def test_invalid_amount_falls_back_to_none(
    dynamic_pricing_on, pin_station_origin
):
    """A garbage Total_Ammount value (e.g. None / non-numeric) must
    not propagate — returns None rather than a bogus quote."""
    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {
            "Total_Ammount": "not-a-number"
        }
        quote = adapter.live_quote(
            rate=None, country_code="GR", weight_grams=None, currency="EUR"
        )

    assert quote is None


# ---------------------------------------------------------------------------
# Weight-aware quote tests
#
# The legacy floor sent ``Weight: "0,5"`` regardless of the cart — so a
# 4 kg cart got quoted at the 0.5 kg bracket and then upcharged at
# voucher mint. The bucketing helper + ``_fetch_live_quote`` weight
# forwarding fix that. Cover both pieces here so the fix can't silently
# regress.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weight_grams,expected_bucket",
    [
        (None, 500),
        (0, 500),
        (-100, 500),
        (1, 500),
        (499, 500),
        (500, 500),
        (501, 1000),
        (1000, 1000),
        (1001, 2000),
        (2000, 2000),
        (2001, 3000),
        (3000, 3000),
        (3500, 4000),
        (5999, 6000),
        (6000, 6000),
        (6001, 7000),
        (7500, 8000),
        (12345, 13000),
    ],
)
def test_bucket_weight_grams_matches_acs_brackets(
    weight_grams, expected_bucket
):
    """The bucketing must mirror the ACS published tariff steps —
    500g floor, 1 kg bracket, 2 kg bracket, then 1 kg increments. Off-
    by-one errors show up as upstream cache thrashing or undercharging.
    """
    from shipping_acs.carrier import AcsCarrier

    assert AcsCarrier._bucket_weight_grams(weight_grams) == expected_bucket


def test_live_quote_forwards_bucketed_weight_to_acs(
    dynamic_pricing_on, pin_station_origin
):
    """Heavy cart → bucketed weight reaches the ACS price-calculation
    endpoint via ``_kg_from_grams``. The voucher mint uses the SAME
    helper, so quote and charge line up exactly — the assertion below
    walks the bucket through ``_kg_from_grams`` rather than hardcoding
    a literal so a future locale-format tweak fails one place, not two.
    """
    from shipping_acs.services import _kg_from_grams

    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {"Total_Ammount": 5.21}
        adapter.live_quote(
            rate=None,
            country_code="GR",
            weight_grams=3200,
            currency="EUR",
        )

    assert instance.price_calculation.called
    payload = instance.price_calculation.call_args[0][0]
    # 3.2 kg buckets to 4 kg → whatever ``_kg_from_grams`` formats it as.
    assert payload["Weight"] == _kg_from_grams(4000)
    # Sanity: quote + voucher mint must NEVER diverge here.
    assert payload["Weight"] != _kg_from_grams(3200)  # raw weight not sent


def test_live_quote_floor_when_weight_omitted(
    dynamic_pricing_on, pin_station_origin
):
    """Caller without weight info gets the historical 500g floor —
    the same behaviour ACS's published tariff applies on its side
    so the cheapest possible bracket is what shows in the sidebar.
    """
    from shipping_acs.services import _kg_from_grams

    adapter = get_provider("acs")

    with patch("shipping_acs.client.AcsClient") as mock_class:
        instance = mock_class.return_value
        instance.price_calculation.return_value = {"Total_Ammount": 3.50}
        adapter.live_quote(
            rate=None,
            country_code="GR",
            weight_grams=None,
            currency="EUR",
        )

    payload = instance.price_calculation.call_args[0][0]
    # 500g formatted via _kg_from_grams (Greek-locale comma-decimal).
    assert payload["Weight"] == _kg_from_grams(500)


def test_quote_cache_buckets_collapse_near_weights(
    dynamic_pricing_on, pin_station_origin
):
    """487g and 499g hit the same 500g bucket → one upstream call,
    not two. Without the bucketing the cache key would diverge per
    gram and ACS's API would be hammered.
    """
    import uuid
    from unittest.mock import MagicMock

    unique_country = f"W{uuid.uuid4().hex[:6]}"
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
        instance.price_calculation.return_value = {"Total_Ammount": 3.50}

        for weight in (200, 487, 499, 500):
            adapter.live_quote(
                rate=None,
                country_code=unique_country,
                region_id="A",
                weight_grams=weight,
                currency="EUR",
            )

    assert instance.price_calculation.call_count == 1
    assert len(fake_store) == 1


def test_live_quote_sends_station_origin_and_destination(dynamic_pricing_on):
    """``ACS_Price_Calculation`` returns ``Άγνωστο κατάστημα παραλαβής``
    when ``Acs_Station_Origin`` is missing — even with a valid billing
    code. The carrier resolves origin from
    ``shipping_acs.config.station_origin()`` (parsed from the billing
    code by default) and uses it for both endpoints when no per-call
    destination is supplied.
    """
    from shipping_acs import config as acs_config

    adapter = get_provider("acs")

    with (
        patch.object(acs_config, "station_origin", return_value="ΑΚ"),
        patch("shipping_acs.client.AcsClient") as mock_class,
    ):
        instance = mock_class.return_value
        instance.price_calculation.return_value = {"Total_Ammount": 2.30}
        adapter.live_quote(
            rate=None,
            country_code="GR",
            weight_grams=2000,
            currency="EUR",
        )

    assert instance.price_calculation.called
    payload = instance.price_calculation.call_args[0][0]
    assert payload["Acs_Station_Origin"] == "ΑΚ"
    assert payload["Acs_Station_Destination"] == "ΑΚ"


def test_live_quote_falls_back_when_station_origin_missing(dynamic_pricing_on):
    """No billing code + no metadata override → adapter must return
    ``None`` instead of calling ACS with empty origin (which would
    always return the same business error)."""
    from shipping_acs import config as acs_config

    adapter = get_provider("acs")

    with (
        patch.object(acs_config, "station_origin", return_value=None),
        patch("shipping_acs.client.AcsClient") as mock_class,
    ):
        instance = mock_class.return_value
        quote = adapter.live_quote(
            rate=None,
            country_code="GR",
            weight_grams=2000,
            currency="EUR",
        )

    assert instance.price_calculation.called is False
    assert quote is None


def test_live_quote_business_error_falls_back_to_none(dynamic_pricing_on):
    """ACS returns 200 with ``Error_Message`` populated (not an
    exception) for unknown-station rejections. The adapter must
    treat that as ``None``, not propagate the empty ``Total_Ammount``
    as a 0€ quote."""
    from shipping_acs import config as acs_config

    adapter = get_provider("acs")

    with (
        patch.object(acs_config, "station_origin", return_value="ΑΚ"),
        patch("shipping_acs.client.AcsClient") as mock_class,
    ):
        instance = mock_class.return_value
        instance.price_calculation.return_value = {
            "Total_Ammount": None,
            "Basic_Ammount": None,
            "Error_Message": "Άγνωστο κατάστημα παραλαβής.",
        }
        quote = adapter.live_quote(
            rate=None,
            country_code="GR",
            weight_grams=2000,
            currency="EUR",
        )

    assert quote is None


def test_station_origin_parses_from_billing_code(monkeypatch):
    """Billing code format ``<category><station><customer>`` — the
    helper extracts positions 1-2 as the station code. Greek-locale
    billing codes (``2ΑΚ89587``) are common in Greece.

    ``acs_billing_code`` is tenant-only (no settings fallback), so the
    fake tenant is bound directly on ``connection.tenant`` rather than
    via ``override_settings``.
    """
    from types import SimpleNamespace

    from django.db import connection

    from shipping_acs import config as acs_config

    def _bind(billing_code: str) -> None:
        monkeypatch.setattr(
            connection,
            "tenant",
            SimpleNamespace(acs_billing_code=billing_code),
            raising=False,
        )

    _bind("2ΑΚ89587")
    assert acs_config.station_origin() == "ΑΚ"

    _bind("2ΘΕ12345")
    assert acs_config.station_origin() == "ΘΕ"

    _bind("")
    assert acs_config.station_origin() is None

    _bind("2")  # too short
    assert acs_config.station_origin() is None


def test_quote_cache_separates_distinct_weight_buckets(
    dynamic_pricing_on, pin_station_origin
):
    """487g and 1500g hit different buckets (500g / 2 kg) → two
    upstream calls. Without bucket-keyed caching a heavy cart would
    silently reuse the light cart's quote.
    """
    import uuid
    from unittest.mock import MagicMock

    unique_country = f"W{uuid.uuid4().hex[:6]}"
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
        instance.price_calculation.return_value = {"Total_Ammount": 4.20}

        adapter.live_quote(
            rate=None,
            country_code=unique_country,
            region_id="A",
            weight_grams=487,
            currency="EUR",
        )
        adapter.live_quote(
            rate=None,
            country_code=unique_country,
            region_id="A",
            weight_grams=1500,
            currency="EUR",
        )

    assert instance.price_calculation.call_count == 2
    assert len(fake_store) == 2
