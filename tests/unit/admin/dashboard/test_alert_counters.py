"""The "Needs attention" counters behind the operational alert emails.

A store may mute an alert email (``shipping.alerts.OpsAlertKind``). That is
only safe because the work each email reports stays counted on the
dashboard by the very rule the email uses, so every counter here is
compared with its email's rule, and a muted email still shows its count.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money

from admin.dashboard.store import queries, widgets
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from shipping.alerts import OpsAlertKind
from shipping_acs.attention import UNATTRIBUTED_PAYOUT_DAYS
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout, AcsShipment
from shipping_acs.tasks import (
    _unremitted_cod_rows,
    alert_unremitted_cod_payouts,
    check_stale_acs_shipments,
)
from tests.utils.orders import courier_cash_order
from tests.utils.staff import stamp_platform_identity
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture(autouse=True)
def _operators(settings):
    settings.ADMINS = [("Ops", "ops@example.com")]
    cache.clear()


@pytest.fixture(autouse=True)
def _acs_configured():
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


def _counts() -> dict[str, int]:
    return queries.attention_counts.compute()


def _shipment(*, days_ago: int = 5, **fields):
    defaults = {
        "voucher_no": "9700000001",
        "shipment_state": AcsShipmentState.NEW,
        "last_event_at": timezone.now() - timedelta(days=days_ago),
    }
    defaults.update(fields)
    return AcsShipmentFactory(**defaults)


def _cod_shipment(*, days_ago: int, voucher: str):
    return AcsShipmentFactory(
        order=OrderFactory(
            status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PENDING
        ),
        voucher_no=voucher,
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount=Money(Decimal("24.48"), "EUR"),
        delivery_date=timezone.now() - timedelta(days=days_ago),
    )


def _payout(voucher: str, **fields):
    return AcsCodPayout.objects.create(
        voucher_no=voucher,
        cod_payment_date=timezone.localdate(),
        cod_amount_total=Money(Decimal("24.48"), "EUR"),
        **fields,
    )


class TestStaleShipments:
    def test_counts_what_the_digest_reports(self):
        _shipment(voucher_no="9700000001")
        _shipment(voucher_no="9700000002", days_ago=40)
        # Not stale: fresh, terminal.
        _shipment(voucher_no="9700000003", days_ago=1)
        _shipment(
            voucher_no="9700000004",
            shipment_state=AcsShipmentState.DELIVERED,
        )
        stranded = AcsShipmentFactory(
            order=courier_cash_order(),
            shipment_state=AcsShipmentState.PENDING_CREATION,
        )
        AcsShipment.objects.filter(pk=stranded.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )

        with patch("shipping.alerts.send_mail"):
            alerted = check_stale_acs_shipments.run()["alerted"]

        assert alerted == 3
        assert _counts()["stale_shipments"] == alerted

    def test_an_alerted_parcel_stays_counted_until_someone_handles_it(self):
        _shipment(stale_alert_sent=True)

        assert _counts()["stale_shipments"] == 1

    def test_an_unpaid_online_order_is_not_a_stranded_mint(self):
        unpaid = AcsShipmentFactory(
            order=OrderFactory(
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                pay_way=PayWayFactory.create_online_payment(),
            ),
            shipment_state=AcsShipmentState.PENDING_CREATION,
        )
        AcsShipment.objects.filter(pk=unpaid.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )

        assert _counts()["stale_shipments"] == 0

    def test_a_muted_digest_leaves_the_count(self):
        _shipment()
        switch_store_settings(
            **{OpsAlertKind.STALE_SHIPMENTS.setting_name: False}
        )

        with patch("shipping.alerts.send_mail") as mail:
            check_stale_acs_shipments.run()

        mail.assert_not_called()
        assert _counts()["stale_shipments"] == 1


class TestUnremittedCod:
    def test_counts_what_the_nightly_alert_lists(self):
        _cod_shipment(days_ago=30, voucher="9772892603")
        _cod_shipment(days_ago=20, voucher="9775922531")
        # Remitted, inside the normal lag, paid outside ACS: not owed.
        _payout(_cod_shipment(days_ago=30, voucher="9780000001").voucher_no)
        _cod_shipment(days_ago=3, voucher="9780000002")
        outside = _cod_shipment(days_ago=30, voucher="9780000003")
        AcsShipment.objects.filter(pk=outside.pk).update(
            cod_received_outside_acs_at=timezone.now()
        )

        with patch("shipping.alerts.send_ops_alert", return_value=True):
            listed = alert_unremitted_cod_payouts.run()["unremitted"]

        assert listed == 2
        assert len(_unremitted_cod_rows(10)) == 2
        assert _counts()["unremitted_cod"] == 2

    def test_a_muted_alert_leaves_the_count(self):
        _cod_shipment(days_ago=30, voucher="9772892603")
        switch_store_settings(
            **{OpsAlertKind.UNREMITTED_COD.setting_name: False}
        )

        with patch("shipping.alerts.send_mail") as mail:
            alert_unremitted_cod_payouts.run()

        mail.assert_not_called()
        assert _counts()["unremitted_cod"] == 1


class TestUnattributedPayouts:
    def test_a_recent_payout_for_no_shipment_is_counted(self):
        _payout("2431665832")

        assert _counts()["unattributed_cod_payouts"] == 1

    def test_a_payout_tied_to_a_shipment_is_not(self):
        _payout("9772892603", shipment=_shipment(voucher_no="9772892603"))

        assert _counts()["unattributed_cod_payouts"] == 0

    def test_nothing_clears_an_orphan_so_the_count_is_a_window(self):
        old = _payout("2417791935")
        AcsCodPayout.objects.filter(pk=old.pk).update(
            created_at=timezone.now()
            - timedelta(days=UNATTRIBUTED_PAYOUT_DAYS + 1)
        )
        _payout("2431665832")

        assert _counts()["unattributed_cod_payouts"] == 1


class TestCanceledStillPaid:
    def _order(self, **fields):
        return OrderFactory(num_order_items=0, **fields)

    def test_counts_canceled_orders_whose_payment_was_not_returned(self):
        self._order(
            status=OrderStatus.CANCELED, payment_status=PaymentStatus.COMPLETED
        )
        self._order(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        )
        self._order(
            status=OrderStatus.CANCELED, payment_status=PaymentStatus.REFUNDED
        )
        self._order(
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )

        assert _counts()["canceled_still_paid"] == 2

    def test_a_payment_after_cancel_is_counted_until_it_is_refunded(self):
        order = self._order(
            status=OrderStatus.CANCELED, payment_status=PaymentStatus.CANCELED
        )
        assert _counts()["canceled_still_paid"] == 0

        switch_store_settings(
            **{OpsAlertKind.PAYMENT_AFTER_CANCEL.setting_name: False}
        )
        with patch("shipping.alerts.send_mail") as mail:
            OrderService.record_payment_after_cancel(
                order, payment_id="pi_late", payment_method="stripe"
            )

        mail.assert_not_called()
        assert _counts()["canceled_still_paid"] == 1

        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.REFUNDED
        )
        assert _counts()["canceled_still_paid"] == 0


def _superuser():
    return stamp_platform_identity(UserAccountFactory(admin=True))


def _rows() -> dict[str, dict]:
    request = RequestFactory().get("/admin/")
    request.user = _superuser()
    return {
        str(row["label"]): row
        for row in widgets.StoreAttention(request=request).get_context_data()[
            "rows"
        ]
    }


class TestDashboardRows:
    def test_each_counter_is_a_row_that_opens_its_list(self):
        _shipment()
        _cod_shipment(days_ago=30, voucher="9772892603")
        _payout("2431665832")
        OrderFactory(
            num_order_items=0,
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.COMPLETED,
        )

        rows = _rows()

        assert rows["Stale ACS shipments"]["href"].endswith("attention=stale")
        assert rows["COD cash ACS has not paid out"]["href"].endswith(
            "attention=unremitted_cod"
        )
        assert (
            "unattributed=yes"
            in rows["ACS payouts matching no shipment"]["href"]
        )
        assert (
            "created_at__gte="
            in rows["ACS payouts matching no shipment"]["href"]
        )
        assert rows["Canceled orders to refund"]["href"].endswith(
            "refund_owed=yes"
        )

    def test_nothing_waiting_draws_no_row(self):
        labels = set(_rows())

        assert not labels & {
            "Stale ACS shipments",
            "COD cash ACS has not paid out",
            "ACS payouts matching no shipment",
            "Canceled orders to refund",
        }


@pytest.fixture
def client():
    user = User.objects.create_superuser(
        username="boss", email="boss@example.com", password="x"
    )
    client = Client()
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    return client


def _listed(client, url_name: str, query: str) -> set[int]:
    response = client.get(f"{reverse(url_name)}?{query}")
    assert response.status_code == 200
    return {row.pk for row in response.context["cl"].result_list}


class TestTheListsTheRowsOpen:
    def test_the_shipment_list_shows_exactly_what_was_counted(self, client):
        stale = _shipment(voucher_no="9700000001")
        _shipment(voucher_no="9700000002", days_ago=1)
        unremitted = _cod_shipment(days_ago=30, voucher="9772892603")

        changelist = "admin:shipping_acs_acsshipment_changelist"

        assert _listed(client, changelist, "attention=stale") == {stale.pk}
        assert _listed(client, changelist, "attention=unremitted_cod") == {
            unremitted.pk
        }

    def test_the_payout_list_shows_the_orphans(self, client):
        orphan = _payout("2431665832")
        _payout("9772892603", shipment=_shipment(voucher_no="9772892603"))

        assert _listed(
            client,
            "admin:shipping_acs_acscodpayout_changelist",
            "unattributed=yes",
        ) == {orphan.pk}
