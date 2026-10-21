"""A freshly provisioned store can take an order.

``Order.pay_way`` is required at checkout, and provisioning used to seed
no payment methods, so a new store could not accept a single order until
someone created rows by hand. ``pay_way`` 0019 seeds the three defaults
and 0021 BOX NOW PAY ON THE GO; 0020 backfills their ``settlement`` and
0029 moves their key onto the row.

These used to be checked by replaying 0019 and 0020 against today's
``PayWay``, patched to look like the models those migrations were
written for. This lane migrates a real store schema through that history
with the historical models, so it checks what a new store actually gets.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_tenants.utils import schema_context

from order.payment import get_payment_provider
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement

# provider_code -> (active, settlement). Only cash on delivery ships
# usable: the online providers' credentials live on the Tenant row and are
# empty for a new store, and PAY ON THE GO needs a BOX NOW contract.
SEEDED = {
    "cash_on_delivery": (True, PaySettlement.COURIER_CASH),
    "viva_wallet": (False, PaySettlement.ONLINE),
    "stripe": (False, PaySettlement.ONLINE),
    "boxnow_pay_on_the_go": (False, PaySettlement.CARRIER_TERMINAL),
}


def _seeded(tenant):
    from pay_way.models import PayWay

    with schema_context(tenant.schema_name):
        return list(PayWay.objects.filter(provider_code__in=SEEDED))


@pytest.mark.django_db
def test_seeds_each_default_once_with_its_state(mt_tenant):
    rows = _seeded(mt_tenant)

    assert sorted(p.provider_code for p in rows) == sorted(SEEDED)
    assert {
        p.provider_code: (p.active, PaySettlement(p.settlement)) for p in rows
    } == SEEDED


@pytest.mark.django_db
def test_online_codes_match_the_payment_provider_registry(mt_tenant):
    """A code the registry does not know fails mid-checkout.

    ``ImproperlyConfigured`` is the expected outcome, and the reason these
    are seeded inactive: a provider refuses to construct without the
    store's credentials. Only ``ValueError`` ("Unknown payment provider")
    means a bad code.
    """
    online = [
        p for p in _seeded(mt_tenant) if p.settlement == PaySettlement.ONLINE
    ]
    assert online
    for pay_way in online:
        try:
            get_payment_provider(pay_way.provider_code)
        except ImproperlyConfigured:
            continue
        except ValueError as exc:
            pytest.fail(
                f"{pay_way.provider_code!r} is not registered in "
                f"order.payment.get_payment_provider: {exc}"
            )


@pytest.mark.django_db
def test_keys_are_enum_keys_the_storefront_can_translate(mt_tenant):
    """The storefront renders ``payment_methods.<KEY>`` from its locale, so
    a display string stored here would bypass translation."""
    valid = set(PayWayEnum.values)
    for pay_way in _seeded(mt_tenant):
        assert pay_way.key in valid, (
            f"{pay_way.provider_code} -> {pay_way.key!r}"
        )
