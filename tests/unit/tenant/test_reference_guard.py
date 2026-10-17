"""The delete guard's parts that need no tenant schema.

The cross-schema behaviour (orders, addresses, rates and pay-way
exclusions in a store's own schema) is covered in
``tests_mt/test_reference_data_delete_guard.py``; ``tests/`` has the
public schema only.
"""

from __future__ import annotations

import pytest
from django.db.models import ProtectedError
from simple_history.models import HistoricalChanges

from country.factories import CountryFactory
from region.factories import RegionFactory
from tenant.reference_guard import StoreReferences, _reverse_relations
from tests.utils.staff import store_tenant

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _owners(model) -> set[str]:
    return {r.related_model._meta.label for r in _reverse_relations(model)}


def test_the_relations_checked_are_the_ones_a_store_owns():
    from country.models import Country

    owners = _owners(Country)

    # Found through the hidden (``related_name="+"``) relations too.
    assert {
        "shipping.ShippingRate",
        "pay_way.PayWayShippingExclusion",
    } <= owners
    assert {"order.Order", "user.UserAddress"} <= owners
    # Deleted with the country, never a store's: no translations, no
    # regions, and no simple-history copy of a rate.
    assert not {"country.CountryTranslation", "region.Region"} & owners
    assert not any(
        issubclass(r.related_model, HistoricalChanges)
        for r in _reverse_relations(Country)
    )


def test_a_region_is_checked_through_its_own_relations():
    from region.models import Region

    assert {"order.Order", "user.UserAddress"} <= _owners(Region)


def test_the_message_names_the_store_and_what_it_holds():
    refs = StoreReferences(
        store="Alpha Shop", counts={"orders": 3, "shipping rates": 1}
    )

    assert str(refs) == "Alpha Shop: used by 3 orders, 1 shipping rates"


def test_a_store_without_a_schema_does_not_block_a_delete():
    """The test database has no store schemas (offboarded stores look the
    same), so nothing can be in use and the stock behaviour is unchanged."""
    from country.models import Country

    store_tenant("guard_no_schema")
    country = CountryFactory()

    country.delete()

    assert not Country.objects.filter(pk=country.pk).exists()


def test_the_guard_runs_before_any_delete_of_reference_data(monkeypatch):
    """The ``pre_delete`` backstop is what stops a delete issued outside
    the admin; when it reports a store, nothing is removed."""
    from country.models import Country
    from tenant import signals

    country = CountryFactory()
    monkeypatch.setattr(
        signals,
        "references_to",
        lambda instances: [StoreReferences("Alpha Shop", {"orders": 1})],
    )

    with pytest.raises(ProtectedError, match="Alpha Shop"):
        country.delete()

    assert Country.objects.filter(pk=country.pk).exists()


def test_a_region_delete_is_guarded_too(monkeypatch):
    from region.models import Region
    from tenant import signals

    region = RegionFactory()
    monkeypatch.setattr(
        signals,
        "references_to",
        lambda instances: [StoreReferences("Alpha Shop", {"orders": 1})],
    )

    with pytest.raises(ProtectedError):
        region.delete()

    assert Region.objects.filter(pk=region.pk).exists()
