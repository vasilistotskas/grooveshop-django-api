"""The order tells its client how long it can be paid and when it is decided.

``paymentHoldExpiresAt`` is when the payment link stops being payable and
``paymentDecidedBy`` the latest instant by which the sweep has canceled the
order if it is still unpaid: the run that selects it, plus that run's hard
time limit. The agent gateway publishes them to the platform that placed the
order, so it holds no window of its own (issue #636).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.db.models import Q
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from extra_settings.models import Setting
from rest_framework.test import APIClient

from order import payment_hold
from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.attribution import OrderAttribution
from order.models.order import AMOUNT_MISMATCH_FLAG, Order
from order.tasks import auto_cancel_stuck_pending_orders
from pay_way.factories import PayWayFactory
from scheduler.models import PeriodicTask
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

SECRET = "gateway-secret"

#: The sweep sets no limit of its own, so the worker's applies.
LIMIT = timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)


def _set(name: str, value: int) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": "int", "value_int": value},
    )


@pytest.fixture(autouse=True)
def _windows():
    """The shipped defaults: 30 minutes' hold, 5 grace, 30 after a failure."""
    _set(payment_hold.HOLD_SETTING, 30)
    _set(payment_hold.GRACE_SETTING, 5)
    _set(payment_hold.MAX_HOLD_SETTING, 60)
    _set(payment_hold.FAILED_SETTING, 30)


@pytest.fixture(autouse=True)
def _sweep_on():
    """Beat's row for the sweep, switched on, as beat creates it."""
    PeriodicTask.objects.create(name=payment_hold.SWEEP_ENTRY)


@pytest.fixture
def hour() -> datetime:
    """A whole hour still to come, so every instant a test builds on it is
    ahead of the clock: the sweep runs at :00, :15, :30, :45 (every zone
    the schedule may run in is offset by whole hours)."""
    return timezone.now().replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=2)


def _order(*, created=None, hold=None, updated=None, **kwargs):
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    kwargs.setdefault(
        "pay_way", PayWayFactory.create_online_payment("viva_wallet")
    )
    kwargs.setdefault("user", None)
    order = OrderFactory(num_order_items=0, **kwargs)
    fields = {"payment_hold_expires_at": hold}
    if created is not None:
        fields["created_at"] = created
    if updated is not None:
        fields["updated_at"] = updated
    Order.objects.filter(pk=order.pk).update(**fields)
    order.refresh_from_db()
    return order


class TestTheHoldEnds:
    def test_when_the_last_link_expires(self, hour):
        order = _order(hold=hour + timedelta(minutes=7))

        assert payment_hold.hold_ends(order) == hour + timedelta(minutes=7)

    def test_from_the_order_before_any_link(self, hour):
        order = _order(created=hour)

        assert payment_hold.hold_ends(order) == hour + timedelta(minutes=30)

    def test_the_store_chooses_the_hold(self, hour):
        _set(payment_hold.HOLD_SETTING, 45)
        order = _order(created=hour)

        assert payment_hold.hold_ends(order) == hour + timedelta(minutes=45)


class TestTheOrderIsDecidedByTheSweep:
    def test_the_first_run_after_the_hold_and_the_grace(self, hour):
        # 0:07 + 5 grace = 0:12, so the 0:15 run.
        order = _order(hold=hour + timedelta(minutes=7))

        assert (
            payment_hold.decided_by(order)
            == hour + timedelta(minutes=15) + LIMIT
        )

    def test_a_run_at_the_very_instant_does_not_select_it(self, hour):
        # 0:10 + 5 = 0:15: the sweep selects only what ended before it ran.
        order = _order(hold=hour + timedelta(minutes=10))

        assert (
            payment_hold.decided_by(order)
            == hour + timedelta(minutes=30) + LIMIT
        )

    def test_the_store_chooses_the_grace(self, hour):
        _set(payment_hold.GRACE_SETTING, 20)
        order = _order(hold=hour + timedelta(minutes=7))

        assert (
            payment_hold.decided_by(order)
            == hour + timedelta(minutes=30) + LIMIT
        )

    def test_with_no_link_it_runs_from_the_order(self, hour):
        # 0:00 + 30 hold + 5 grace = 0:35, so the 0:45 run.
        order = _order(created=hour)

        assert (
            payment_hold.decided_by(order)
            == hour + timedelta(minutes=45) + LIMIT
        )

    def test_it_is_the_run_the_sweep_selects_the_order_in(self, hour):
        order = _order(hold=hour + timedelta(minutes=7))
        decided = payment_hold.decided_by(order)
        assert decided is not None
        run = decided - LIMIT
        run_before = run - timedelta(minutes=15)

        selected = payment_hold.expired_unpaid_online_orders(now=run)
        not_yet = payment_hold.expired_unpaid_online_orders(now=run_before)

        assert order in selected
        assert order not in not_yet

    def test_a_failed_payment_waits_for_its_retry_window(self, hour):
        # Hold ends 0:07 (+5 = 0:12), the failure at 0:10 (+30 = 0:40):
        # the later of the two, so the 0:45 run.
        order = _order(
            hold=hour + timedelta(minutes=7),
            updated=hour + timedelta(minutes=10),
            payment_status=PaymentStatus.FAILED,
        )

        assert (
            payment_hold.decided_by(order)
            == hour + timedelta(minutes=45) + LIMIT
        )

    def test_a_failed_payment_waits_for_the_link_too(self, hour):
        # The failure 0:00 (+30 = 0:30), the link until 0:47 (+5 = 0:52).
        order = _order(
            hold=hour + timedelta(minutes=47),
            updated=hour,
            payment_status=PaymentStatus.FAILED,
        )

        assert (
            payment_hold.decided_by(order) == hour + timedelta(hours=1) + LIMIT
        )

    def test_a_failure_with_no_online_pay_way_is_decided_too(self, hour):
        # The failed bucket takes any pay way, and cancels at once what has
        # no payment to close; the hold bounds it from above.
        for pay_way in (None, PayWayFactory.create_offline_payment()):
            order = _order(
                hold=hour + timedelta(minutes=7),
                updated=hour + timedelta(minutes=10),
                payment_status=PaymentStatus.FAILED,
                pay_way=pay_way,
            )
            decided = payment_hold.decided_by(order)
            assert decided == hour + timedelta(minutes=45) + LIMIT
            run = decided - LIMIT
            assert order in payment_hold.expired_failed_payment_orders(now=run)

    def test_the_sweep_runs_on_the_declared_schedule(self):
        entry = settings.CELERY_BEAT_SCHEDULE[payment_hold.SWEEP_ENTRY]

        assert entry["task"] == (
            "tenant.tasks.fanout_auto_cancel_stuck_pending_orders"
        )
        assert isinstance(entry["schedule"], crontab)

    def test_the_sweep_runs_under_the_worker_s_time_limit(self):
        # The worker kills a run at the task's ``time_limit`` when the
        # message carries none (celery.worker.request), and the fanout
        # sends none. ``Task.bind`` fills the attribute from
        # ``task_time_limit``, which decided_by reads, when the task sets
        # none, so this reads the limit the worker enforces: one of the
        # task's own would show here, and decided_by would have to read it.
        assert auto_cancel_stuck_pending_orders.time_limit == (
            payment_hold.celery_app.conf.task_time_limit
        )

    def test_the_worker_s_time_limit_is_the_margin(self, hour):
        # 0:07 + 5 grace = 0:12, so the 0:15 run, plus what the worker reads.
        order = _order(hold=hour + timedelta(minutes=7))
        limit = payment_hold.celery_app.conf.task_time_limit

        assert payment_hold.decided_by(order) == (
            hour + timedelta(minutes=15) + timedelta(seconds=limit)
        )
        assert timedelta(seconds=limit) == LIMIT

    def test_the_sweep_s_soft_limit_does_not_end_the_run(self):
        # The run catches Exception per order, so only the hard limit counts.
        from celery.exceptions import SoftTimeLimitExceeded

        assert issubclass(SoftTimeLimitExceeded, Exception)


class TestTheSweepCancelsExactlyTheSharedDefinition:
    """``auto_cancel_stuck_pending_orders`` cancels what
    ``payment_hold.swept_orders`` holds once its time has come, and nothing
    else, so ``paymentDecidedBy`` covers exactly what the sweep decides."""

    def test_every_bucket_and_exclusion(self):
        now = timezone.now()
        past = now - timedelta(hours=2)
        future = now + timedelta(hours=2)
        cash = PayWayFactory.create_offline_payment()
        failed = PaymentStatus.FAILED
        orders = {
            "pending_online_expired": _order(hold=past),
            "failed_online_past_window": _order(
                hold=past, updated=past, payment_status=failed
            ),
            "failed_cash_past_window": _order(
                hold=past, updated=past, payment_status=failed, pay_way=cash
            ),
            "failed_no_pay_way_past_window": _order(
                hold=past, updated=past, payment_status=failed, pay_way=None
            ),
            "failed_online_link_still_payable": _order(
                hold=future, updated=past, payment_status=failed
            ),
            "failed_within_window": _order(
                hold=past, updated=now, payment_status=failed
            ),
            "processing": _order(
                hold=past, payment_status=PaymentStatus.PROCESSING
            ),
            "amount_mismatch": _order(
                hold=past, metadata={AMOUNT_MISMATCH_FLAG: True}
            ),
            "settled": _order(
                hold=past,
                status=OrderStatus.PROCESSING,
                payment_status=PaymentStatus.COMPLETED,
            ),
            "not_yet_due": _order(hold=future),
        }
        due = set(
            payment_hold.swept_orders().filter(
                Q(pk__in=payment_hold.expired_unpaid_online_orders(now))
                | Q(pk__in=payment_hold.expired_failed_payment_orders(now))
            )
        )
        # A Viva payment order stays payable until its hold ends, so the
        # sweep keeps this one open (``close_payment``) though it is due.
        kept_open = orders["failed_online_link_still_payable"]
        assert kept_open in due

        auto_cancel_stuck_pending_orders()

        canceled = {
            order
            for order in orders.values()
            if Order.objects.get(pk=order.pk).status == OrderStatus.CANCELED
        }
        assert canceled == due - {kept_open}
        assert {name for name, o in orders.items() if o in canceled} == {
            "pending_online_expired",
            "failed_online_past_window",
            "failed_cash_past_window",
            "failed_no_pay_way_past_window",
        }
        kept_open.refresh_from_db()
        assert kept_open.status == OrderStatus.PENDING

    def test_the_sweep_selects_through_the_shared_definition(self):
        """Narrow ``swept_orders`` and the sweep follows: it holds no
        exclusion of its own that could drift from ``paymentDecidedBy``."""
        past = timezone.now() - timedelta(hours=2)
        kept = _order(hold=past)
        swept = _order(hold=past)
        shared = payment_hold.swept_orders

        with patch.object(
            payment_hold,
            "swept_orders",
            lambda: shared().exclude(pk=kept.pk),
        ):
            auto_cancel_stuck_pending_orders()

        kept.refresh_from_db()
        swept.refresh_from_db()
        assert kept.status == OrderStatus.PENDING
        assert swept.status == OrderStatus.CANCELED

    def test_the_warning_lists_only_charges_waiting_for_the_store(self, caplog):
        past = timezone.now() - timedelta(hours=2)
        flag = {AMOUNT_MISMATCH_FLAG: True}
        waiting = _order(hold=past, metadata=flag)
        _order(
            hold=past,
            metadata=flag,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.COMPLETED,
        )
        _order(
            hold=past,
            metadata=flag,
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
        )

        with caplog.at_level("WARNING", logger="order.tasks"):
            auto_cancel_stuck_pending_orders()

        skips = [
            r
            for r in caplog.records
            if r.msg.startswith("Skipping auto-cancel")
        ]
        assert [r.args for r in skips] == [(1, [waiting.pk])]
        waiting.refresh_from_db()
        assert waiting.status == OrderStatus.PENDING


class TestAnInstantAlreadyPastIsNeverPromised:
    def test_the_sweep_switched_off_across_the_hold_end(self, hour):
        # The 0:15 run that would have selected it was missed while the
        # sweep was off, and a resumed sweep sends no missed slot.
        rows = PeriodicTask.objects.filter(name=payment_hold.SWEEP_ENTRY)
        rows.switch_off()
        order = _order(hold=hour + timedelta(minutes=7))
        assert payment_hold.decided_by(order) is None
        rows.switch_on()

        read_at = hour + timedelta(minutes=50)

        assert payment_hold.decided_by(order, now=read_at) == (
            hour + timedelta(hours=1) + LIMIT
        )

    def test_a_run_that_left_the_order_open(self, hour):
        # Due at the 0:15 run, still PENDING at 0:31: the 0:45 run.
        order = _order(hold=hour + timedelta(minutes=7))

        decided = payment_hold.decided_by(
            order, now=hour + timedelta(minutes=31)
        )

        assert decided == hour + timedelta(minutes=45) + LIMIT


class TestTheRunIsTheOneBeatSendsAcrossAClockChange:
    """Beat steps through the slots on the Athens wall clock: it sends no
    run in the hour the clock goes back, and the slot in the missing spring
    hour runs at 04:00 (``docs/scheduler.md``,
    ``tests/integration/scheduler/test_beat.py``).

    The instants are Athens's 2026 transitions, so the entry's crontab is
    pinned to Athens here: ``CELERY_TIMEZONE`` comes from ``TIME_ZONE``,
    which a local ``.env`` may set to another zone."""

    @pytest.fixture(autouse=True)
    def _athens_beat(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE[payment_hold.SWEEP_ENTRY]
        cls, args, kwargs = entry["schedule"].__reduce__()
        pinned = cls(*args, **(kwargs or {}))
        # ``tz`` is a cached property read from the app: set it first.
        pinned.tz = ZoneInfo("Europe/Athens")
        settings.CELERY_BEAT_SCHEDULE = {
            **settings.CELERY_BEAT_SCHEDULE,
            payment_hold.SWEEP_ENTRY: {**entry, "schedule": pinned},
        }

    @pytest.mark.parametrize(
        ("read_at", "run"),
        [
            pytest.param(
                datetime(2026, 10, 25, 1, 5, tzinfo=UTC),
                datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
                id="inside-the-repeated-hour",
            ),
            pytest.param(
                datetime(2026, 10, 25, 0, 50, tzinfo=UTC),
                datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
                id="the-last-run-before-the-repeated-hour-has-passed",
            ),
            pytest.param(
                datetime(2026, 10, 25, 0, 20, tzinfo=UTC),
                datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
                id="the-limit-counts-real-time-across-the-change",
            ),
            pytest.param(
                datetime(2026, 3, 29, 0, 50, tzinfo=UTC),
                datetime(2026, 3, 29, 1, 0, tzinfo=UTC),
                id="into-the-missing-spring-hour",
            ),
        ],
    )
    def test_the_run_beat_sends(self, read_at, run):
        order = _order(hold=read_at - timedelta(hours=1))

        decided = payment_hold.decided_by(order, now=read_at)

        assert decided == run + LIMIT
        assert decided > read_at


class TestNothingIsPromisedTheSweepWillNotDo:
    def test_the_entry_is_no_longer_declared(self, hour, settings):
        # A deploy removed the entry; its enabled row stays until beat
        # next starts.
        settings.CELERY_BEAT_SCHEDULE = {
            name: entry
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            if name != payment_hold.SWEEP_ENTRY
        }
        order = _order(hold=hour + timedelta(minutes=7))

        assert payment_hold.decided_by(order) is None
        assert payment_hold.hold_ends(order) == hour + timedelta(minutes=7)

    def test_the_sweep_is_switched_off(self, hour):
        PeriodicTask.objects.filter(name=payment_hold.SWEEP_ENTRY).switch_off()
        order = _order(hold=hour + timedelta(minutes=7))

        assert payment_hold.decided_by(order) is None
        assert payment_hold.hold_ends(order) == hour + timedelta(minutes=7)

    def test_beat_has_not_created_its_row(self, hour):
        PeriodicTask.objects.filter(name=payment_hold.SWEEP_ENTRY).delete()
        order = _order(hold=hour + timedelta(minutes=7))

        assert payment_hold.decided_by(order) is None

    def test_a_payment_the_provider_is_processing(self, hour):
        order = _order(
            hold=hour + timedelta(minutes=7),
            payment_status=PaymentStatus.PROCESSING,
        )

        assert payment_hold.decided_by(order) is None
        assert payment_hold.awaiting_payment(order) is True

    def test_a_charge_that_did_not_match_the_total(self, hour):
        order = _order(
            hold=hour + timedelta(minutes=7),
            metadata={AMOUNT_MISMATCH_FLAG: True},
        )

        assert payment_hold.decided_by(order) is None

    @pytest.mark.parametrize(
        ("status", "payment_status"),
        [
            (OrderStatus.PENDING, PaymentStatus.COMPLETED),
            (OrderStatus.PROCESSING, PaymentStatus.COMPLETED),
            (OrderStatus.CANCELED, PaymentStatus.CANCELED),
        ],
    )
    def test_a_settled_order(self, hour, status, payment_status):
        order = _order(
            hold=hour + timedelta(minutes=7),
            status=status,
            payment_status=payment_status,
        )

        assert payment_hold.awaiting_payment(order) is False
        assert payment_hold.decided_by(order) is None

    def test_cash_on_delivery(self, hour):
        order = _order(
            created=hour,
            pay_way=PayWayFactory.create_offline_payment(),
        )

        assert payment_hold.awaiting_payment(order) is False
        assert payment_hold.decided_by(order) is None


class TestTheOrderDetailServesBoth:
    """The guest route and the gateway's own read share the detail."""

    @staticmethod
    def _instant(value: str | None) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)

    def _guest(self, order) -> dict:
        response = APIClient().get(
            reverse("order-retrieve-by-uuid", kwargs={"uuid": order.uuid}),
            {"uuid": str(order.uuid)},
        )
        assert response.status_code == 200, response.data
        return response.json()

    def _gateway(self, order) -> dict:
        OrderAttribution.objects.create(
            order=order, source_type=OrderSourceType.AGENT, source="ucp"
        )
        with override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET):
            response = APIClient().get(
                reverse("agent-internal-order", kwargs={"uuid": order.uuid}),
                headers={"X-Internal-Token": SECRET},
            )
        assert response.status_code == 200, response.data
        return response.json()

    @pytest.mark.parametrize("read", ["_guest", "_gateway"])
    def test_an_unpaid_online_order(self, hour, read):
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        body = getattr(self, read)(order)

        assert self._instant(body["paymentHoldExpiresAt"]) == (
            hour + timedelta(minutes=37)
        )
        assert self._instant(body["paymentDecidedBy"]) == (
            hour + timedelta(minutes=45) + LIMIT
        )

    def test_the_sweep_switched_off(self, hour):
        PeriodicTask.objects.filter(name=payment_hold.SWEEP_ENTRY).switch_off()
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        body = self._guest(order)

        assert body["paymentHoldExpiresAt"] is not None
        assert body["paymentDecidedBy"] is None

    def test_the_entry_no_longer_declared(self, hour, settings):
        settings.CELERY_BEAT_SCHEDULE = {
            name: entry
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            if name != payment_hold.SWEEP_ENTRY
        }
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        body = self._guest(order)

        assert body["paymentHoldExpiresAt"] is not None
        assert body["paymentDecidedBy"] is None

    @pytest.mark.parametrize("read", ["_guest", "_gateway"])
    def test_a_paid_order(self, hour, read):
        order = _order(
            created=hour,
            hold=hour + timedelta(minutes=37),
            payment_status=PaymentStatus.COMPLETED,
        )

        body = getattr(self, read)(order)

        assert body["paymentHoldExpiresAt"] is None
        assert body["paymentDecidedBy"] is None

    def test_a_payment_in_processing_keeps_its_link_expiry(self, hour):
        order = _order(
            created=hour,
            hold=hour + timedelta(minutes=37),
            payment_status=PaymentStatus.PROCESSING,
        )

        body = self._guest(order)

        assert self._instant(body["paymentHoldExpiresAt"]) == (
            hour + timedelta(minutes=37)
        )
        assert body["paymentDecidedBy"] is None


class TestAChargeThatWaitsForTheStoreIsToldApart:
    """``paymentAwaitsReview`` tells a mismatched charge the store must
    reconcile from an order no sweep decides for another reason (#739)."""

    _guest = TestTheOrderDetailServesBoth._guest
    _gateway = TestTheOrderDetailServesBoth._gateway

    @pytest.mark.parametrize("read", ["_guest", "_gateway"])
    @pytest.mark.parametrize(
        "payment_status",
        [PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.PROCESSING],
    )
    def test_a_flagged_undecided_order(self, hour, read, payment_status):
        order = _order(
            created=hour,
            hold=hour + timedelta(minutes=37),
            payment_status=payment_status,
            metadata={AMOUNT_MISMATCH_FLAG: True},
        )

        body = getattr(self, read)(order)

        assert body["paymentAwaitsReview"] is True
        assert body["paymentDecidedBy"] is None
        assert order in payment_hold.payment_review_orders()

    @pytest.mark.parametrize(
        ("status", "payment_status"),
        [
            # Settled by hand: marked paid, then fulfilled.
            (OrderStatus.PENDING, PaymentStatus.COMPLETED),
            (OrderStatus.PROCESSING, PaymentStatus.COMPLETED),
            # Canceled: ``Order.settle_unpaid_payment`` closes the payment.
            (OrderStatus.CANCELED, PaymentStatus.CANCELED),
            # Refunded.
            (OrderStatus.CANCELED, PaymentStatus.REFUNDED),
            (OrderStatus.PENDING, PaymentStatus.REFUNDED),
            (OrderStatus.REFUNDED, PaymentStatus.REFUNDED),
        ],
    )
    def test_a_flagged_order_the_store_has_decided(
        self, hour, status, payment_status
    ):
        order = _order(
            created=hour,
            hold=hour + timedelta(minutes=37),
            status=status,
            payment_status=payment_status,
            metadata={AMOUNT_MISMATCH_FLAG: True},
        )

        body = self._guest(order)

        assert body["paymentAwaitsReview"] is False
        assert order not in payment_hold.payment_review_orders()

    def test_a_flagged_order_canceled_by_the_store(self, hour):
        order = _order(
            created=hour,
            hold=hour + timedelta(minutes=37),
            metadata={AMOUNT_MISMATCH_FLAG: True},
        )
        assert payment_hold.awaits_payment_review(order) is True

        order.status = OrderStatus.CANCELED
        order.save()
        order.refresh_from_db()

        assert AMOUNT_MISMATCH_FLAG in order.metadata
        assert order.payment_status == PaymentStatus.CANCELED
        assert self._guest(order)["paymentAwaitsReview"] is False

    def test_an_unflagged_order_with_the_sweep_switched_off(self, hour):
        PeriodicTask.objects.filter(name=payment_hold.SWEEP_ENTRY).switch_off()
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        body = self._guest(order)

        assert body["paymentDecidedBy"] is None
        assert body["paymentAwaitsReview"] is False

    def test_an_unflagged_order_asks_the_database_nothing(
        self, hour, django_assert_num_queries
    ):
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        with django_assert_num_queries(0):
            assert payment_hold.awaits_payment_review(order) is False

    def test_an_unflagged_order_the_sweep_decides(self, hour):
        order = _order(created=hour, hold=hour + timedelta(minutes=37))

        body = self._guest(order)

        assert body["paymentDecidedBy"] is not None
        assert body["paymentAwaitsReview"] is False


class TestTheCheckoutSessionReturnsTheLinkExpiry:
    def test_expires_at(self):
        user = UserAccountFactory(num_addresses=0)
        order = _order(
            user=user,
            pay_way=PayWayFactory.create_online_payment("viva_wallet"),
        )
        applied = (timezone.now() + timedelta(minutes=30)).replace(
            microsecond=0
        )
        provider = Mock()
        provider.create_checkout_session.return_value = (
            True,
            {
                "session_id": "S1",
                "checkout_url": "https://pay.example/S1",
                "status": "created",
                "amount": "10.00",
                "currency": "EUR",
                "provider": "viva_wallet",
                "expires_at": applied,
            },
        )
        provider.checkout_session_params.return_value = {}
        provider.min_hosted_session = timedelta(0)
        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(user=user)

        with (
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
            patch(
                "order.views.order.get_payment_provider", return_value=provider
            ),
        ):
            response = client.post(
                reverse(
                    "order-create-checkout-session", kwargs={"pk": order.pk}
                ),
                {
                    "successUrl": "https://shop.example/ok",
                    "cancelUrl": "https://shop.example/no",
                },
                format="json",
            )

        assert response.status_code == 200, response.data
        assert datetime.fromisoformat(response.json()["expiresAt"]) == applied
        order.refresh_from_db()
        assert order.payment_hold_expires_at == applied


class TestTheGraceIsValidated:
    @pytest.mark.parametrize(
        ("value", "ok"),
        [
            (-1, False),
            (0, True),
            (5, True),
            (60, True),
            (61, False),
            ("5", False),
            (True, False),
            (None, False),
        ],
    )
    def test_bounds(self, value, ok):
        assert payment_hold.validate_grace_minutes_setting(value) is ok

    def test_declared_on_the_setting(self):
        (entry,) = (
            item
            for item in settings.EXTRA_SETTINGS_DEFAULTS
            if item["name"] == payment_hold.GRACE_SETTING
        )

        assert entry["validator"] == (
            "order.setting_validators.validate_grace_minutes_setting"
        )
        assert payment_hold.validate_grace_minutes_setting(entry["value"])
