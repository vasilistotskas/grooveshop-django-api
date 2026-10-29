"""The conditions under which an email type is sent at all.

A type lists its gates and every one must be open. A closed gate turns the
send into a skip whose reason is the gate's ``label``, shown to the store
as "Off: <label>". Four kinds exist because the conditions are not all one
table: a store feature, a server-side store setting, an active carrier, and
a scheduled job that is switched on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django_stubs_ext import StrOrPromise
from django_tenants.utils import get_public_schema_name, schema_context

from core.store_settings import declared_default, store_setting
from tenant.features import STORE_FEATURES, feature_on


@dataclass(frozen=True, kw_only=True)
class Gate(ABC):
    #: Why the type is off while the gate is closed, as a short noun
    #: phrase the store reads ("Gift cards feature").
    label: StrOrPromise

    @abstractmethod
    def is_open(self) -> bool: ...


@dataclass(frozen=True, kw_only=True)
class FeatureGate(Gate):
    """A store feature of ``tenant.features.STORE_FEATURES``."""

    feature: str

    def __post_init__(self) -> None:
        if self.feature not in STORE_FEATURES:
            raise ImproperlyConfigured(
                f"{self.feature!r} is not a store feature "
                "(tenant.features.STORE_FEATURES)."
            )

    def is_open(self) -> bool:
        return feature_on(self.feature)


@dataclass(frozen=True, kw_only=True)
class StoreSwitchGate(Gate):
    """A server-side store setting no client reads (class (s) in
    ``docs/store-features.md``), such as ``MYDATA_ENABLED``."""

    setting: str

    def __post_init__(self) -> None:
        declared_default(self.setting)

    def is_open(self) -> bool:
        return bool(store_setting(self.setting))


@dataclass(frozen=True, kw_only=True)
class CarrierGate(Gate):
    """A shipping carrier whose ``ShippingProvider`` row is active."""

    carrier: str

    def is_open(self) -> bool:
        from shipping.models import ShippingProvider

        return ShippingProvider.objects.filter(
            code=self.carrier, is_active=True
        ).exists()


@dataclass(frozen=True, kw_only=True)
class ScheduleGate(Gate):
    """A beat entry that is enabled. Beat entries live in the public
    schema, whichever store the send is for."""

    entry: str

    def is_open(self) -> bool:
        from django_celery_beat.models import PeriodicTask

        with schema_context(get_public_schema_name()):
            return PeriodicTask.objects.filter(
                name=self.entry, enabled=True
            ).exists()
