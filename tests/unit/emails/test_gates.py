"""The four gates: each opens and closes on the fact it names."""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from django_tenants.utils import get_public_schema_name, schema_context

from emails.gates import CarrierGate, FeatureGate, ScheduleGate, StoreSwitchGate
from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


class TestFeatureGate:
    def test_it_follows_the_stores_feature(self):
        gate = FeatureGate(feature="gift_cards", label="Gift cards")

        with mock.patch("emails.gates.feature_on", return_value=True) as on:
            assert gate.is_open() is True
        on.assert_called_once_with("gift_cards")
        with mock.patch("emails.gates.feature_on", return_value=False):
            assert gate.is_open() is False

    def test_a_name_that_is_not_a_feature_is_refused_when_declared(self):
        with pytest.raises(ImproperlyConfigured, match="not_a_feature"):
            FeatureGate(feature="not_a_feature", label="x")


class TestStoreSwitchGate:
    @pytest.mark.parametrize("value", [True, False])
    def test_it_follows_the_setting(self, value):
        gate = StoreSwitchGate(setting="MYDATA_ENABLED", label="myDATA")

        with mock.patch(
            "extra_settings.models.Setting.get", return_value=value
        ):
            assert gate.is_open() is value

    def test_a_setting_that_is_not_declared_is_refused_when_declared(self):
        with pytest.raises(KeyError, match="NOT_A_SETTING"):
            StoreSwitchGate(setting="NOT_A_SETTING", label="x")


class TestCarrierGate:
    def test_it_is_open_only_while_the_carrier_is_active(self):
        gate = CarrierGate(carrier="acs", label="ACS")
        ShippingProvider.objects.update_or_create(
            code="acs", defaults={"name": "ACS", "is_active": False}
        )
        assert gate.is_open() is False

        ShippingProvider.objects.filter(code="acs").update(is_active=True)

        assert gate.is_open() is True

    def test_another_active_carrier_does_not_open_it(self):
        ShippingProvider.objects.update_or_create(
            code="boxnow", defaults={"name": "BOX NOW", "is_active": True}
        )
        ShippingProvider.objects.filter(code="acs").delete()

        assert CarrierGate(carrier="acs", label="ACS").is_open() is False


class TestScheduleGate:
    def test_it_is_open_only_while_the_beat_entry_is_enabled(self):
        gate = ScheduleGate(entry="probe-entry", label="Scheduled job")
        assert gate.is_open() is False

        with schema_context(get_public_schema_name()):
            entry = PeriodicTask.objects.create(
                name="probe-entry",
                task="probe.task",
                enabled=False,
                interval=IntervalSchedule.objects.create(
                    every=1, period=IntervalSchedule.DAYS
                ),
            )
        assert gate.is_open() is False

        entry.enabled = True
        entry.save()

        assert gate.is_open() is True
