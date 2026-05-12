"""Phase 0 regression guard: the ACS shipping config must come from
``ShippingProvider.metadata`` so admins can change shop_kinds, the
nearest-search limit, and weight bounds without a redeploy.

If any of these tests fail, you've reintroduced a hardcoded value
that the seed migration was meant to eliminate. The migration is
``shipping/migrations/0004_seed_provider_metadata.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from shipping.models import ShippingProvider
from shipping_acs import config as acs_config
from shipping_acs.factories import AcsStationFactory


@pytest.fixture
def acs_provider():
    """Return the seeded ACS provider row, asserting metadata exists."""
    provider = ShippingProvider.objects.get(code="acs")
    assert provider.metadata, (
        "ACS metadata is empty — the 0004_seed_provider_metadata "
        "migration didn't run or the conftest reseed fixture didn't "
        "restore it. Either of those breaks every test below."
    )
    return provider


@pytest.mark.django_db
class TestShopKinds:
    def test_metadata_kinds_drive_country_filter(self, acs_provider):
        acs_provider.metadata = {
            **acs_provider.metadata,
            "shop_kinds_by_country": {"GR": [99], "CY": [88]},
        }
        acs_provider.save(update_fields=["metadata"])

        assert acs_config.shop_kinds_for_country("GR") == (99,)
        assert acs_config.shop_kinds_for_country("CY") == (88,)

    def test_unknown_country_falls_back_to_first_listed(self, acs_provider):
        acs_provider.metadata = {
            **acs_provider.metadata,
            "shop_kinds_by_country": {"GR": [7, 8]},
        }
        acs_provider.save(update_fields=["metadata"])

        # IT (Italy) isn't configured — fall back to GR's kinds rather
        # than crash.
        assert acs_config.shop_kinds_for_country("IT") == (7, 8)

    def test_all_locker_kinds_unions_across_countries(self, acs_provider):
        acs_provider.metadata = {
            **acs_provider.metadata,
            "shop_kinds_by_country": {"GR": [7, 8], "CY": [7, 9]},
        }
        acs_provider.save(update_fields=["metadata"])

        assert acs_config.all_locker_kinds() == (7, 8, 9)


@pytest.mark.django_db
class TestNearestLimit:
    def test_limit_drives_endpoint_cap(self, acs_provider):
        # Spin up 6 stations all in the same postal-code prefix
        for i in range(6):
            AcsStationFactory(
                external_id=f"LIMIT_{i}",
                postal_code="10552",
                country_code="GR",
                shop_kind=8,
                is_active=True,
            )

        acs_provider.metadata = {**acs_provider.metadata, "nearest_limit": 3}
        acs_provider.save(update_fields=["metadata"])

        client = APIClient()
        response = client.get(
            "/api/v1/shipping/acs/stations/nearest",
            {"postalCode": "10552"},
        )
        assert response.status_code == 200
        # Cap was 20 before; metadata override drops it to 3.
        assert len(response.data) == 3

    def test_invalid_limit_falls_back_to_default(self, acs_provider):
        acs_provider.metadata = {
            **acs_provider.metadata,
            "nearest_limit": "not-an-int",
        }
        acs_provider.save(update_fields=["metadata"])

        assert acs_config.nearest_limit() == 20


@pytest.mark.django_db
class TestWeightBounds:
    def test_min_kg_clamps_kg_from_grams(self, acs_provider):
        from shipping_acs.services import _kg_from_grams

        acs_provider.metadata = {
            **acs_provider.metadata,
            "min_weight_kg": "1.5",
        }
        acs_provider.save(update_fields=["metadata"])

        # 100 g would normally clamp to 0.5 kg; with min raised to 1.5
        # the clamp-up is 1.5.
        assert _kg_from_grams(100) == "1,5"

    def test_max_kg_clamps_kg_from_grams(self, acs_provider):
        from shipping_acs.services import _kg_from_grams

        acs_provider.metadata = {
            **acs_provider.metadata,
            "max_weight_kg": "10",
        }
        acs_provider.save(update_fields=["metadata"])

        # 50 kg shouldn't reach ACS — clamp to 10. The helper always
        # adds a ",0" suffix when the result has no fractional part
        # so ACS's parser still treats it as a kg decimal.
        assert _kg_from_grams(50_000) == "10,0"

    def test_decimal_returned(self, acs_provider):
        assert isinstance(acs_config.min_voucher_weight_kg(), Decimal)
        assert isinstance(acs_config.max_voucher_weight_kg(), Decimal)


@pytest.mark.django_db
class TestDefaultCountry:
    def test_setting_first_wins(self, settings, acs_provider):
        settings.ACS_SUPPORTED_COUNTRIES = ["CY", "GR"]
        assert acs_config.default_country() == "CY"

    def test_falls_back_to_metadata_then_gr(self, settings, acs_provider):
        settings.ACS_SUPPORTED_COUNTRIES = []
        acs_provider.metadata = {
            "shop_kinds_by_country": {"IT": [7]},
        }
        acs_provider.save(update_fields=["metadata"])
        assert acs_config.default_country() == "IT"


@pytest.mark.django_db
class TestMapConfig:
    def test_map_config_passes_metadata_through(self, acs_provider):
        cfg = acs_config.map_config()
        assert cfg["default_map_center"] == [37.9838, 23.7275]
        assert cfg["default_map_zoom"] == 11
        assert "tile_provider" in cfg
        assert "light" in cfg["tile_provider"]
        assert "dark" in cfg["tile_provider"]


@pytest.mark.django_db
class TestVoucherLanguage:
    def test_metadata_overrides_language(self, acs_provider):
        acs_provider.metadata = {
            **acs_provider.metadata,
            "default_voucher_language": "EN",
        }
        acs_provider.save(update_fields=["metadata"])
        assert acs_config.default_voucher_language() == "EN"


@pytest.mark.django_db
class TestPrintType:
    def test_default_is_thermal(self, acs_provider):
        # Per ACS PDF: 1 = thermal/roll printer (single voucher per
        # page); 2 = laser (4 vouchers per A4). Ops uses thermal so
        # 1 is our default — fail loud if anyone reverts it.
        assert acs_config.print_type() == 1

    def test_metadata_can_flip_to_laser(self, acs_provider):
        acs_provider.metadata = {**acs_provider.metadata, "print_type": 2}
        acs_provider.save(update_fields=["metadata"])
        assert acs_config.print_type() == 2

    def test_invalid_value_falls_back_to_default(self, acs_provider):
        for bad in ("garbage", None, 0, 3, 99):
            acs_provider.metadata = {**acs_provider.metadata, "print_type": bad}
            acs_provider.save(update_fields=["metadata"])
            assert acs_config.print_type() == 1, (
                f"print_type={bad!r} should clamp to default; got "
                f"{acs_config.print_type()}"
            )

    def test_fetch_label_bytes_passes_print_type_and_keys_cache_by_it(
        self, acs_provider
    ):
        # Cache key must include the print type so flipping the
        # metadata invalidates only the layout that changed —
        # otherwise admins flipping thermal→laser would still get
        # the cached thermal PDF for an hour.
        from unittest.mock import patch
        from django.core.cache import cache
        from shipping_acs.factories import AcsShipmentFactory
        from shipping_acs.services import AcsService

        shipment = AcsShipmentFactory(voucher_no="TEST_9999999999")
        cache.delete("acs:label:TEST_9999999999:pt1")
        cache.delete("acs:label:TEST_9999999999:pt2")

        with patch("shipping_acs.client.AcsClient.print_voucher") as mock_print:
            mock_print.return_value = b"%PDF-1.7 thermal"
            # Default thermal call.
            assert AcsService.fetch_label_bytes(shipment) == b"%PDF-1.7 thermal"
            mock_print.assert_called_once_with("TEST_9999999999", print_type=1)

            # Flip provider metadata to laser; cache miss → fresh call
            # with print_type=2.
            mock_print.reset_mock()
            mock_print.return_value = b"%PDF-1.7 laser"
            acs_provider.metadata = {
                **acs_provider.metadata,
                "print_type": 2,
            }
            acs_provider.save(update_fields=["metadata"])

            assert AcsService.fetch_label_bytes(shipment) == b"%PDF-1.7 laser"
            mock_print.assert_called_once_with("TEST_9999999999", print_type=2)
