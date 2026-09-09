"""BOX NOW PAY ON THE GO must not be labelled "Αντικαταβολή".

The storefront renders a pay-way by looking its name token up as
``payment_methods.<name>``, so the token IS the button copy. ``0021``
seeded the new row with ``PAY_ON_DELIVERY`` — the courier-cash row's
token — and checkout drew two radio buttons reading "Αντικαταβολή":
€1,99 cash to a courier, and €0 by card at a locker terminal. Nothing
on screen told them apart.

BoxNow requires the product appear as its own option
("να εμφανίζεται ως ξεχωριστό κουμπί στο checkout"), which is why
``0021`` created a separate row at all. ``0022`` finishes the job.
"""

from __future__ import annotations

import importlib

import pytest

from pay_way.enum.pay_way import PayWayEnum
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay

MIGRATION = "pay_way.migrations.0022_boxnow_potg_distinct_name"
PROVIDER_CODE = "boxnow_pay_on_the_go"


class _SchemaEditor:
    class connection:
        alias = "default"


@pytest.fixture
def relabel():
    module = importlib.import_module(MIGRATION)

    def _run():
        from django.apps import apps

        module.relabel_pay_on_the_go(apps, _SchemaEditor)

    return _run


@pytest.fixture
def restore():
    module = importlib.import_module(MIGRATION)

    def _run():
        from django.apps import apps

        module.restore_seeded_name(apps, _SchemaEditor)

    return _run


def _set_name(pay_way: PayWay, name: str) -> None:
    # ``.update()`` on the translation, not ``set_current_language`` +
    # save: this mirrors what the migration does and keeps the test
    # independent of parler's active-language state.
    pay_way.translations.update(name=name)


def _name_of(pay_way: PayWay) -> str:
    return pay_way.translations.values_list("name", flat=True).first()


@pytest.mark.django_db
class TestTheTokenIsDistinct:
    def test_the_enum_carries_a_token_of_its_own(self):
        assert PayWayEnum.BOX_NOW_PAY_ON_THE_GO != PayWayEnum.PAY_ON_DELIVERY
        assert PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value in {
            value for value, _label in PayWayEnum.choices
        }

    def test_the_seeded_row_is_relabelled(self, relabel):
        pay_way = PayWayFactory.create_carrier_terminal_payment(
            provider_code=PROVIDER_CODE
        )
        _set_name(pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

        relabel()

        assert _name_of(pay_way) == PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value

    def test_the_courier_cash_row_is_left_alone(self, relabel):
        """It is the legitimate owner of ``PAY_ON_DELIVERY``."""
        courier = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
        )
        _set_name(courier, PayWayEnum.PAY_ON_DELIVERY.value)

        relabel()

        assert _name_of(courier) == PayWayEnum.PAY_ON_DELIVERY.value

    def test_an_operator_rename_survives(self, relabel):
        """Same rule as the ``get_or_create`` seeders it follows.

        A merchant who renamed the row from the admin has made a
        decision about their own storefront copy; a later deploy must
        not overwrite it.
        """
        pay_way = PayWayFactory.create_carrier_terminal_payment(
            provider_code=PROVIDER_CODE
        )
        _set_name(pay_way, PayWayEnum.VIVA_WALLET.value)

        relabel()

        assert _name_of(pay_way) == PayWayEnum.VIVA_WALLET.value

    def test_reverse_restores_only_what_it_wrote(self, relabel, restore):
        pay_way = PayWayFactory.create_carrier_terminal_payment(
            provider_code=PROVIDER_CODE
        )
        _set_name(pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

        relabel()
        restore()

        assert _name_of(pay_way) == PayWayEnum.PAY_ON_DELIVERY.value

    def test_reverse_leaves_an_operator_rename_alone(self, restore):
        pay_way = PayWayFactory.create_carrier_terminal_payment(
            provider_code=PROVIDER_CODE
        )
        _set_name(pay_way, PayWayEnum.VIVA_WALLET.value)

        restore()

        assert _name_of(pay_way) == PayWayEnum.VIVA_WALLET.value
