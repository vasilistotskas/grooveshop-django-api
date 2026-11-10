"""Voucher requests wait while ACS is failing (#631).

After #626 a voucher request whose answer is lost is never sent again;
staff check that order at the ACS portal. In an ACS outage every order
of the outage would end that way, one check each. So the first lost
answer begins an ``AcsVoucherHold`` for the store: nothing more is sent
to ``ACS_Create_Voucher``, the orders are marked as held (nothing
reached ACS, so they are not of unknown outcome), and
``issue_held_acs_vouchers`` sends them once ACS answers again, one
request first.

ACS is the HTTP session here: the assertions are on what was POSTed.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.contrib.admin.models import LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils import timezone

from order.enum.status import OrderStatus
from shipping_acs import attention, services, voucher_hold
from shipping_acs.admin import AcsShipmentAdmin, AcsVoucherHoldAdmin
from shipping_acs.client import AcsClient
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import (
    AcsRetryableError,
    AcsVoucherRequestHeldError,
    AcsVoucherRequestUnknownError,
)
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsApiCall, AcsShipment, AcsVoucherHold
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    create_acs_voucher_for_order,
    issue_held_acs_vouchers,
)
from tests.utils.orders import courier_cash_order

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

CREATE = "ACS_Create_Voucher"
PROBE = "ACS_Get_Pickup_Lists"
DELETE = "ACS_Delete_Voucher"


def _response(status_code: int, body=None, *, text: str | None = None):
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    if body is None:
        response.text = text or ""
        response.json.side_effect = ValueError("not JSON")
    else:
        response.text = json.dumps(body)
        response.json.return_value = body
    return response


def _answer(value_row: dict | None = None, table: list | None = None):
    return _response(
        200,
        {
            "ACSExecution_HasError": False,
            "ACSExecutionErrorMessage": "",
            "ACSOutputResponce": {
                "ACSValueOutput": [value_row or {"Error_Message": None}],
                "ACSTableOutput": {"Table_Data": table or []},
            },
        },
    )


class _Acs:
    """ACS behind the HTTP session: answers by alias, records every POST."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.create: list = []
        self.probe = _answer()
        self.vouchers = iter(range(7401573000, 7401574000))

    def post(self, url, *, json, headers, timeout):
        self.posts.append(json)
        alias = json["ACSAlias"]
        if alias == PROBE:
            return self._give(self.probe)
        if alias == DELETE:
            return _answer()
        assert alias == CREATE, alias
        if self.create:
            return self._give(self.create.pop(0))
        return _answer({"Voucher_No": str(next(self.vouchers))})

    @staticmethod
    def _give(outcome):
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome) and not isinstance(outcome, MagicMock):
            return outcome()
        return outcome

    def aliases(self) -> list[str]:
        return [post["ACSAlias"] for post in self.posts]

    def orders_requested(self) -> list[str]:
        return [
            post["ACSInputParameters"]["Reference_Key1"]
            for post in self.posts
            if post["ACSAlias"] == CREATE
        ]


@pytest.fixture
def acs(monkeypatch):
    remote = _Acs()

    def client() -> AcsClient:
        return AcsClient(
            api_key="TEST_API_KEY",
            company_id="TEST_COMPANY",
            company_password="TEST_PASS",
            user_id="TEST_USER",
            user_password="TEST_USER_PASS",
            billing_code="TEST_BILLING",
            api_base_url="https://example/acs",
            session=remote,
        )

    monkeypatch.setattr(services, "AcsClient", client)
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield remote


@pytest.fixture
def sent():
    with patch("shipping_acs.services.send_email") as send_email:
        yield send_email


def _shipment(**kwargs):
    kwargs.setdefault("order", courier_cash_order(status=OrderStatus.PENDING))
    return AcsShipmentFactory(
        voucher_no=None,
        shipment_state=AcsShipmentState.PENDING_CREATION,
        **kwargs,
    )


def _read_timeout() -> requests.ReadTimeout:
    return requests.ReadTimeout("Read timed out. (read timeout=15)")


def _lose_one_answer(acs, outcome=None):
    """One voucher request reaches ACS and its answer is lost."""
    acs.create.append(outcome or _response(502, text="Bad Gateway"))
    shipment = _shipment()
    result = create_acs_voucher_for_order.run(shipment.order_id)
    assert result["status"] == "voucher_request_unknown"
    return shipment


def _hold() -> AcsVoucherHold:
    return AcsVoucherHold.objects.get(ended_at__isnull=True)


def _wait_is_over(hold: AcsVoucherHold | None = None) -> None:
    hold = hold or _hold()
    AcsVoucherHold.objects.filter(pk=hold.pk).update(
        last_failure_at=timezone.now()
        - timedelta(seconds=3600 * 2 ** (hold.failed_trials + 1))
    )


class TestALostAnswerBeginsAHold:
    @pytest.mark.parametrize(
        "lost",
        [
            lambda: _response(502, text="Bad Gateway"),
            lambda: _response(503, text="Service Unavailable"),
            _read_timeout,
            lambda: requests.ConnectionError("Connection aborted."),
            lambda: _response(200, text="<html>maintenance</html>"),
            # A success with neither a voucher number nor a refusal.
            lambda: _answer({"Voucher_No": None, "Error_Message": None}),
        ],
        ids=["502", "503", "read-timeout", "dropped", "not-json", "no-number"],
    )
    def test_every_unknown_outcome_of_a_create_begins_it(self, acs, sent, lost):
        shipment = _lose_one_answer(acs, lost())

        hold = _hold()
        assert hold.trial_allowed_at is None
        assert hold.failed_trials == 0
        assert hold.reason
        # #626 holds: that order is one of unknown outcome, alerted once.
        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        assert shipment.voucher_request_held_at is None
        assert [call.args[0] for call in sent.call_args_list] == [
            "ops.acs_voucher_request_unknown"
        ]

    def test_a_request_that_never_reached_acs_begins_none(self, acs, sent):
        acs.create.append(requests.ConnectTimeout("connect timed out"))
        shipment = _shipment()

        with pytest.raises(AcsRetryableError):
            create_acs_voucher_for_order.run(shipment.order_id)

        assert not AcsVoucherHold.objects.exists()

    def test_a_refusal_by_acs_begins_none(self, acs, sent):
        acs.create.append(
            _answer({"Voucher_No": None, "Error_Message": "Λάθος ΤΚ"})
        )
        shipment = _shipment()

        result = create_acs_voucher_for_order.run(shipment.order_id)

        assert result["status"] == "acs_api_error"
        assert not AcsVoucherHold.objects.exists()

    def test_an_expired_claim_begins_none(self, acs, sent):
        """A worker that stopped mid-request says nothing about ACS now."""
        shipment = _shipment(
            metadata={
                "mint_started_at": (
                    timezone.now() - timedelta(seconds=400)
                ).isoformat()
            }
        )

        result = create_acs_voucher_for_order.run(shipment.order_id)

        assert result["status"] == "voucher_request_unknown"
        assert not AcsVoucherHold.objects.exists()

    def test_a_second_lost_answer_keeps_the_one_hold(self, acs, sent):
        _lose_one_answer(acs)
        first = _hold()

        voucher_hold.record_unknown_outcome("ACS server error 504")

        hold = _hold()
        assert hold.pk == first.pk
        assert hold.started_at == first.started_at
        assert hold.reason == "ACS server error 504"
        # Not a failed first request: none was allowed yet.
        assert hold.failed_trials == 0


class TestWhileHeldNothingIsSent:
    def test_the_next_order_is_not_sent_and_stays_retryable(self, acs, sent):
        _lose_one_answer(acs)
        sent.reset_mock()
        waiting = _shipment()

        result = create_acs_voucher_for_order.run(waiting.order_id)

        assert result["status"] == "voucher_request_held"
        assert acs.aliases() == [CREATE]  # only the one that was lost
        waiting.refresh_from_db()
        assert waiting.voucher_request_held_at is not None
        assert waiting.voucher_request_unknown_at is None
        assert waiting.voucher_no is None
        assert waiting.shipment_state == AcsShipmentState.PENDING_CREATION
        # No claim to expire into the unknown state, no request an
        # unknown state could belong to, no error recorded.
        assert "mint_started_at" not in (waiting.metadata or {})
        assert "unknown_request" not in (waiting.metadata or {})
        assert "last_error" not in (waiting.metadata or {})
        # Nothing was sent, so it is not a request in flight for the
        # pickup-list run, a delete at ACS or a reset for a re-mint.
        assert not AcsService._mint_in_flight()
        # No per-order alert for a held order.
        sent.assert_not_called()
        # The call history holds the one create that was sent and lost.
        assert AcsApiCall.objects.filter(alias=CREATE).count() == 1

    def test_the_service_says_it_was_held_as_a_retryable_known_outcome(
        self, acs, sent
    ):
        _lose_one_answer(acs)
        waiting = _shipment()

        with pytest.raises(AcsVoucherRequestHeldError) as held:
            AcsService.create_voucher_for_order(waiting.order)

        assert isinstance(held.value, AcsRetryableError)
        assert AcsService._outcome_is_known(held.value)

    def test_a_request_waiting_for_a_slot_when_the_hold_begins_is_not_sent(
        self, acs, sent, monkeypatch
    ):
        """It passed the first check while ACS still looked fine, then
        waited for a request slot behind the request that failed. The
        slot is where "send or not" is decided, so the blast radius is
        the requests already in flight, not the queue behind them."""
        from shipping_acs import client as client_module

        real_slot = client_module.acs_request_slot

        @contextmanager
        def slot_freed_by_the_failed_request(**kwargs):
            voucher_hold.record_unknown_outcome("ACS server error 502")
            with real_slot(**kwargs):
                yield

        monkeypatch.setattr(
            client_module, "acs_request_slot", slot_freed_by_the_failed_request
        )
        waiting = _shipment()

        result = create_acs_voucher_for_order.run(waiting.order_id)

        assert result["status"] == "voucher_request_held"
        assert acs.posts == []
        waiting.refresh_from_db()
        assert waiting.voucher_request_held_at is not None
        assert waiting.voucher_request_unknown_at is None
        # The claim it took is released, or it would expire into the
        # unknown state for a request that was never sent.
        assert "mint_started_at" not in waiting.metadata
        assert not AcsService._mint_in_flight()
        assert "last_error" not in waiting.metadata
        sent.assert_not_called()
        # Not a call to ACS, so the call history shows no failed create.
        assert not AcsApiCall.objects.filter(alias=CREATE).exists()

    def test_a_hold_that_cannot_be_read_sends_nothing_and_is_retried(
        self, acs, sent
    ):
        waiting = _shipment()

        with (
            patch(
                "shipping_acs.voucher_hold.admit",
                side_effect=RuntimeError("no database"),
            ) as asked,
            pytest.raises(AcsRetryableError),
        ):
            AcsService.create_voucher_for_order(waiting.order)

        asked.assert_called_once()
        assert acs.posts == []
        assert not AcsApiCall.objects.filter(alias=CREATE).exists()
        waiting.refresh_from_db()
        assert waiting.voucher_request_unknown_at is None
        assert "mint_started_at" not in waiting.metadata

    def test_other_acs_calls_are_not_held(self, acs, sent):
        _lose_one_answer(acs)

        services.AcsClient().get_pickup_lists(pickup_date="2026-11-10")

        assert acs.aliases() == [CREATE, PROBE]

    def test_an_unknown_outcome_still_wins_over_the_hold(self, acs, sent):
        """A shipment already of unknown outcome is refused as such: the
        hold must never turn it back into something the sweep sends."""
        unknown = _lose_one_answer(acs)

        with pytest.raises(AcsVoucherRequestUnknownError):
            AcsService.create_voucher_for_order(unknown.order)

        unknown.refresh_from_db()
        assert unknown.voucher_request_held_at is None
        assert unknown.pk not in set(
            attention.voucher_requests_held().values_list("pk", flat=True)
        )

    def test_issue_voucher_now_says_the_request_waits(self, acs, sent):
        _lose_one_answer(acs)
        waiting = _shipment()
        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        request = RequestFactory().get("/")
        request.user = get_user_model()(id=1, username="staff")

        with (
            patch("django.contrib.messages.warning") as warned,
            patch("django.contrib.messages.info") as informed,
        ):
            response = admin.issue_voucher_now(request, waiting.pk)

        assert response.status_code == 302
        assert "held" in str(warned.call_args.args[1])
        informed.assert_not_called()
        waiting.refresh_from_db()
        assert waiting.voucher_request_held_at is not None
        assert acs.aliases() == [CREATE]


class TestAskingAcsAgain:
    def test_nothing_is_asked_before_the_wait_is_over(self, acs, sent):
        _lose_one_answer(acs)
        _shipment_held = _shipment()
        create_acs_voucher_for_order.run(_shipment_held.order_id)

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "held", "dispatched": 0}
        assert acs.aliases() == [CREATE]

    def test_a_read_acs_answers_lets_one_request_go_first(self, acs, sent):
        _lose_one_answer(acs)
        first, second = _shipment(), _shipment()
        for shipment in (first, second):
            create_acs_voucher_for_order.run(shipment.order_id)
        _wait_is_over()

        result = issue_held_acs_vouchers.run()

        # One read, then ONE voucher request: the oldest held order.
        assert result == {"status": "one_request_first", "dispatched": 1}
        assert acs.aliases() == [CREATE, PROBE, CREATE]
        assert acs.orders_requested()[-1] == str(first.order_id)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.voucher_no and first.voucher_request_held_at is None
        assert second.voucher_no is None
        assert second.voucher_request_held_at is not None
        # ACS answered it, so the hold is over and the rest follow.
        assert not AcsVoucherHold.objects.filter(ended_at__isnull=True).exists()

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "open", "dispatched": 1}
        second.refresh_from_db()
        assert second.voucher_no and second.voucher_request_held_at is None
        assert acs.aliases() == [CREATE, PROBE, CREATE, CREATE]

    def test_a_read_that_fails_keeps_everything_held(self, acs, sent):
        _lose_one_answer(acs)
        waiting = _shipment()
        create_acs_voucher_for_order.run(waiting.order_id)
        _wait_is_over()
        acs.probe = _response(503, text="Service Unavailable")

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "held", "dispatched": 0}
        assert acs.aliases() == [CREATE, PROBE]
        hold = _hold()
        assert hold.trial_allowed_at is None
        assert hold.failed_trials == 0

    def test_a_first_request_that_fails_closes_the_hold_again(self, acs, sent):
        _lose_one_answer(acs)
        first, second = _shipment(), _shipment()
        for shipment in (first, second):
            create_acs_voucher_for_order.run(shipment.order_id)
        _wait_is_over()
        sent.reset_mock()
        acs.create.append(_read_timeout())
        before = timezone.now()

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "one_request_first", "dispatched": 1}
        hold = _hold()
        assert hold.trial_allowed_at is None
        assert hold.failed_trials == 1
        assert hold.last_failure_at >= before
        # Twice the first wait.
        assert hold.retry_after - hold.last_failure_at == timedelta(seconds=600)
        # Only that one order is left to check at ACS; the other one was
        # never sent and still waits.
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.voucher_request_unknown_at is not None
        assert second.voucher_request_unknown_at is None
        assert second.voucher_request_held_at is not None
        assert acs.orders_requested().count(str(second.order_id)) == 0
        assert [call.args[0] for call in sent.call_args_list] == [
            "ops.acs_voucher_request_unknown"
        ]

        # And the sweep sends nothing until the doubled wait is over.
        assert issue_held_acs_vouchers.run() == {
            "status": "held",
            "dispatched": 0,
        }

    def test_the_wait_doubles_up_to_its_cap(self, settings):
        settings.ACS_VOUCHER_HOLD_RETRY_SECONDS = 300
        settings.ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS = 3600
        now = timezone.now()
        waits = [
            AcsVoucherHold(
                started_at=now, last_failure_at=now, failed_trials=failed
            ).retry_after
            - now
            for failed in (0, 1, 2, 3, 4, 40)
        ]

        assert [wait.total_seconds() for wait in waits] == [
            300,
            600,
            1200,
            2400,
            3600,
            3600,
        ]

    def test_a_second_request_waits_while_the_first_is_in_flight(
        self, acs, sent
    ):
        """One request first, across workers: whoever takes the hold's
        row first is the one request, and a request arriving while it is
        in flight is held."""
        _lose_one_answer(acs)
        first, second = _shipment(), _shipment()
        _wait_is_over()
        assert voucher_hold.allow_trial()
        concurrent: dict = {}

        def while_the_first_is_in_flight():
            concurrent["result"] = create_acs_voucher_for_order.run(
                second.order_id
            )
            concurrent["posts"] = len(acs.posts)
            return _answer({"Voucher_No": "7401579999"})

        acs.create.append(while_the_first_is_in_flight)

        result = create_acs_voucher_for_order.run(first.order_id)

        assert result["status"] == "ok"
        assert concurrent["result"]["status"] == "voucher_request_held"
        # Lost answer, then the one first request; the second was not sent.
        assert concurrent["posts"] == 2
        assert acs.orders_requested()[1:] == [str(first.order_id)]
        second.refresh_from_db()
        assert second.voucher_request_held_at is not None
        assert second.voucher_request_unknown_at is None

    def test_a_first_request_whose_worker_died_does_not_block_the_next(
        self, acs, sent
    ):
        _lose_one_answer(acs)
        _wait_is_over()
        assert voucher_hold.allow_trial()
        voucher_hold.admit(lease_seconds=78)
        blocked = _shipment()
        assert (
            create_acs_voucher_for_order.run(blocked.order_id)["status"]
            == "voucher_request_held"
        )

        AcsVoucherHold.objects.update(
            trial_until=timezone.now() - timedelta(seconds=1)
        )
        result = create_acs_voucher_for_order.run(blocked.order_id)

        assert result["status"] == "ok"
        assert not AcsVoucherHold.objects.filter(ended_at__isnull=True).exists()

    def test_an_answer_while_closed_does_not_end_the_hold(self, acs, sent):
        """A request already in flight when the hold began: its answer
        came from before ACS was asked again, so the wait stands."""
        _lose_one_answer(acs)

        voucher_hold.record_voucher()

        assert _hold().trial_allowed_at is None

    def test_a_request_that_never_reached_acs_leaves_the_hold_as_it_is(
        self, acs, sent
    ):
        _lose_one_answer(acs)
        first = _shipment()
        _wait_is_over()
        assert voucher_hold.allow_trial()
        acs.create.append(requests.ConnectTimeout("connect timed out"))

        with pytest.raises(AcsRetryableError):
            create_acs_voucher_for_order.run(first.order_id)

        hold = _hold()
        assert hold.trial_allowed_at is not None
        assert hold.failed_trials == 0

    def test_a_request_let_through_that_never_connects_stays_held(
        self, acs, sent
    ):
        """Let through as the one request first, and the connection is
        never made, on every attempt the task has. Nothing reached ACS,
        so the order still waits and the sweep is still its retry."""
        _lose_one_answer(acs)
        waiting = _shipment()
        create_acs_voucher_for_order.run(waiting.order_id)
        waiting.refresh_from_db()
        held_since = waiting.voucher_request_held_at
        _wait_is_over()
        assert voucher_hold.allow_trial()

        for _attempt in range(create_acs_voucher_for_order.max_retries + 1):
            # Each retry comes after the turn of the one before it ended.
            AcsVoucherHold.objects.update(trial_until=None)
            acs.create.append(requests.ConnectTimeout("connect timed out"))
            with pytest.raises(AcsRetryableError):
                create_acs_voucher_for_order.run(waiting.order_id)

        waiting.refresh_from_db()
        assert waiting.voucher_request_held_at == held_since
        assert waiting.voucher_request_unknown_at is None
        assert "mint_started_at" not in waiting.metadata
        assert set(
            attention.voucher_requests_held().values_list("pk", flat=True)
        ) == {waiting.pk}

        AcsVoucherHold.objects.update(trial_until=None)
        issue_held_acs_vouchers.run()

        waiting.refresh_from_db()
        assert waiting.voucher_no
        assert waiting.voucher_request_held_at is None
        assert not AcsVoucherHold.objects.filter(ended_at__isnull=True).exists()


class TestWhatEndsAHold:
    """Only a voucher shows that creating one works again.

    A refusal in ACS's own envelope shows ACS executed a create, not that
    one succeeds: the hold stays "one request first", the refused order
    leaves the queue (it is staff's to fix, as before) and the next held
    order goes next. An HTTP 4xx that is not ACS's envelope (a proxy's
    429, a gateway's 404) shows nothing about ACS: like a first request
    that lost its answer, it closes the hold again and doubles the wait.
    """

    @staticmethod
    def _one_request_first(acs):
        _lose_one_answer(acs)
        first, second = _shipment(), _shipment()
        for shipment in (first, second):
            create_acs_voucher_for_order.run(shipment.order_id)
        _wait_is_over()
        return first, second

    @pytest.mark.parametrize(
        "refusal",
        [
            lambda: _answer({"Voucher_No": None, "Error_Message": "Λάθος ΤΚ"}),
            lambda: _response(
                200,
                {
                    "ACSExecution_HasError": True,
                    "ACSExecutionErrorMessage": "Error fill data error",
                },
            ),
        ],
        ids=["value-row-message", "execution-error"],
    )
    def test_a_refusal_by_acs_keeps_the_hold_and_the_next_order_goes_next(
        self, acs, sent, refusal
    ):
        refused, next_one = self._one_request_first(acs)
        acs.create.append(refusal())

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "one_request_first", "dispatched": 1}
        hold = _hold()
        assert hold.trial_allowed_at is not None
        assert hold.failed_trials == 0
        # ACS answered, so the turn is free at once, not after its lease.
        assert hold.trial_until is None
        refused.refresh_from_db()
        assert refused.voucher_request_held_at is None
        assert refused.voucher_request_unknown_at is None

        result = issue_held_acs_vouchers.run()

        # The next held order, never the refused one again.
        assert result == {"status": "one_request_first", "dispatched": 1}
        assert acs.orders_requested().count(str(refused.order_id)) == 1
        assert acs.orders_requested()[-1] == str(next_one.order_id)
        next_one.refresh_from_db()
        assert next_one.voucher_no
        assert not AcsVoucherHold.objects.filter(ended_at__isnull=True).exists()

    @pytest.mark.parametrize("status", [400, 401, 404, 429])
    def test_an_http_error_that_is_not_acs_answering_closes_it_again(
        self, acs, sent, status
    ):
        first, second = self._one_request_first(acs)
        acs.create.append(_response(status, text="<html>proxy</html>"))

        result = issue_held_acs_vouchers.run()

        assert result == {"status": "one_request_first", "dispatched": 1}
        hold = _hold()
        assert hold.trial_allowed_at is None
        assert hold.failed_trials == 1
        assert str(status) in hold.reason
        first.refresh_from_db()
        second.refresh_from_db()
        # A 4xx is a known outcome (#626): that order is rejected, staff's
        # to re-issue, and is not the next request sent first.
        assert first.voucher_request_unknown_at is None
        assert first.voucher_request_held_at is None
        assert second.voucher_request_held_at is not None
        # The other held orders are not sent into it.
        assert issue_held_acs_vouchers.run() == {
            "status": "held",
            "dispatched": 0,
        }
        assert acs.orders_requested().count(str(second.order_id)) == 0

    def test_an_http_error_without_a_hold_begins_none(self, acs, sent):
        acs.create.append(_response(429, text="<html>proxy</html>"))
        shipment = _shipment()

        result = create_acs_voucher_for_order.run(shipment.order_id)

        assert result["status"] == "acs_api_error"
        assert not AcsVoucherHold.objects.exists()

    def test_an_http_error_while_closed_is_not_a_failed_first_request(
        self, acs, sent
    ):
        _lose_one_answer(acs)

        voucher_hold.record_no_answer("ACS HTTP 429")

        assert _hold().failed_trials == 0


class TestARequestOwnsItsClaim:
    """The hold beside the rule that an unknown state belongs to one
    request (#647): the request sent first is a mint like any other, with
    its own claim; a held request has none, so it can own nothing."""

    @staticmethod
    def _one_request_first(acs):
        _lose_one_answer(acs)
        probe = _shipment()
        create_acs_voucher_for_order.run(probe.order_id)
        _wait_is_over()
        assert voucher_hold.allow_trial()
        return probe

    def test_the_request_sent_first_owns_its_unknown_outcome(self, acs, sent):
        probe = self._one_request_first(acs)
        seen = {}

        def lose_it():
            seen["claim"] = AcsShipment.objects.get(pk=probe.pk).metadata[
                "mint_started_at"
            ]
            raise _read_timeout()

        acs.create.append(lose_it)

        result = create_acs_voucher_for_order.run(probe.order_id)

        assert result["status"] == "voucher_request_unknown"
        probe.refresh_from_db()
        assert probe.voucher_request_unknown_at is not None
        assert probe.metadata["unknown_request"] == seen["claim"]
        assert "mint_started_at" not in probe.metadata
        assert probe.voucher_request_held_at is None
        assert _hold().failed_trials == 1

    def test_its_own_late_answer_is_saved_and_ends_the_hold(self, acs, sent):
        probe = self._one_request_first(acs)
        seen = {}

        def answer_late():
            # Its claim outlived the TTL and another worker recorded the
            # outcome as unknown, as the next mint or a pickup-list run
            # does with an expired claim. Then the answer arrives.
            fresh = AcsShipment.objects.get(pk=probe.pk)
            seen["claim"] = fresh.metadata["mint_started_at"]
            AcsService._set_voucher_request_unknown(fresh)
            seen["unknown_request"] = fresh.metadata["unknown_request"]
            return _answer({"Voucher_No": "7401579998"})

        acs.create.append(answer_late)

        result = create_acs_voucher_for_order.run(probe.order_id)

        assert result["status"] == "ok"
        assert seen["unknown_request"] == seen["claim"]
        probe.refresh_from_db()
        assert probe.voucher_no == "7401579998"
        assert probe.voucher_request_unknown_at is None
        assert "unknown_request" not in probe.metadata
        assert probe.voucher_request_held_at is None
        assert not AcsVoucherHold.objects.filter(ended_at__isnull=True).exists()

    def test_a_held_request_takes_no_claim_and_releases_none(self, acs, sent):
        _lose_one_answer(acs)
        waiting = _shipment()

        with (
            patch.object(AcsService, "_release_mint_claim") as released,
            patch.object(AcsService, "_voucher_request_lost") as lost,
        ):
            result = create_acs_voucher_for_order.run(waiting.order_id)

        assert result["status"] == "voucher_request_held"
        released.assert_not_called()
        lost.assert_not_called()
        waiting.refresh_from_db()
        assert "mint_started_at" not in (waiting.metadata or {})
        assert "unknown_request" not in (waiting.metadata or {})

    def test_a_request_stopped_at_its_slot_releases_its_own_claim_only(
        self, acs, sent, monkeypatch
    ):
        from shipping_acs import client as client_module

        real_slot = client_module.acs_request_slot
        real_release = AcsService._release_mint_claim
        releases = []

        @contextmanager
        def slot_freed_by_the_failed_request(**kwargs):
            voucher_hold.record_unknown_outcome("ACS server error 502")
            with real_slot(**kwargs):
                yield

        def release(shipment, claim, **kwargs):
            on_the_row = AcsShipment.objects.get(pk=shipment.pk).metadata.get(
                "mint_started_at"
            )
            releases.append((on_the_row, claim, kwargs))
            return real_release(shipment, claim, **kwargs)

        monkeypatch.setattr(
            client_module, "acs_request_slot", slot_freed_by_the_failed_request
        )
        monkeypatch.setattr(AcsService, "_release_mint_claim", release)
        waiting = _shipment()

        with patch.object(AcsService, "_voucher_request_lost") as lost:
            result = create_acs_voucher_for_order.run(waiting.order_id)

        assert result["status"] == "voucher_request_held"
        lost.assert_not_called()
        [(on_the_row, claim, kwargs)] = releases
        assert claim is not None
        assert claim == on_the_row
        assert kwargs == {"held": True}
        waiting.refresh_from_db()
        assert "mint_started_at" not in waiting.metadata
        assert "unknown_request" not in waiting.metadata

    @staticmethod
    def _later_request_is_held_while_the_first_is_out(acs, answer):
        """Request A is out. Its claim expires and is recorded as unknown;
        staff find no voucher at ACS and request again; that request is
        held, because ACS is failing. Then A is answered with ``answer``."""
        shipment = _shipment()
        later = {}

        def answered_late():
            fresh = AcsShipment.objects.get(pk=shipment.pk)
            AcsService._set_voucher_request_unknown(fresh)
            voucher_hold.record_unknown_outcome("ACS server error 502")
            with patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ):
                AcsService.request_voucher_again(
                    fresh, user=None, note="Checked the ACS portal: none."
                )
            later["result"] = create_acs_voucher_for_order.run(
                shipment.order_id
            )
            return answer

        acs.create.append(answered_late)
        result = create_acs_voucher_for_order.run(shipment.order_id)
        assert later["result"]["status"] == "voucher_request_held"
        shipment.refresh_from_db()
        return shipment, result

    def test_a_stale_voucher_is_deleted_and_the_later_held_request_still_waits(
        self, acs, sent
    ):
        """Phase 3 clears the held stamp only with the voucher it saves.
        A stale answer is deleted at ACS and saves nothing, and the stamp
        on the row is the later request's: the sweep must still send it."""
        shipment, _result = self._later_request_is_held_while_the_first_is_out(
            acs, _answer({"Voucher_No": "7401579997"})
        )

        assert acs.aliases() == [CREATE, DELETE]
        assert shipment.voucher_no is None
        assert shipment.voucher_request_unknown_at is None
        assert shipment.voucher_request_held_at is not None
        assert set(
            attention.voucher_requests_held().values_list("pk", flat=True)
        ) == {shipment.pk}

        _wait_is_over()
        issue_held_acs_vouchers.run()

        shipment.refresh_from_db()
        assert shipment.voucher_no
        assert shipment.voucher_no != "7401579997"
        assert shipment.voucher_request_held_at is None

    def test_a_stale_refusal_leaves_the_later_held_request_waiting(
        self, acs, sent
    ):
        shipment, result = self._later_request_is_held_while_the_first_is_out(
            acs, _answer({"Voucher_No": None, "Error_Message": "Λάθος ΤΚ"})
        )

        assert result["status"] == "acs_api_error"
        assert shipment.voucher_request_held_at is not None
        assert set(
            attention.voucher_requests_held().values_list("pk", flat=True)
        ) == {shipment.pk}


class TestHeldPastTheRetryBudget:
    def test_the_sweep_issues_it_once_acs_answers(self, acs, sent):
        """The task's own retries (5, backoff capped at 600 s) end long
        before a real outage does. A held order does not use them: the
        sweep is its retry, however long the hold lasts."""
        _lose_one_answer(acs)
        waiting = _shipment()

        for _attempt in range(create_acs_voucher_for_order.max_retries + 2):
            result = create_acs_voucher_for_order.run(waiting.order_id)
            assert result["status"] == "voucher_request_held"
        for _tick in range(3):
            assert issue_held_acs_vouchers.run()["dispatched"] == 0
        assert acs.aliases() == [CREATE]

        _wait_is_over()
        issue_held_acs_vouchers.run()

        waiting.refresh_from_db()
        assert waiting.voucher_no
        assert waiting.shipment_state == AcsShipmentState.NEW
        assert waiting.voucher_request_held_at is None
        assert waiting.voucher_request_unknown_at is None
        assert acs.orders_requested().count(str(waiting.order_id)) == 1

    def test_an_order_canceled_while_held_is_never_sent(self, acs, sent):
        _lose_one_answer(acs)
        waiting = _shipment()
        create_acs_voucher_for_order.run(waiting.order_id)
        waiting.order.status = OrderStatus.CANCELED
        waiting.order.save(update_fields=["status"])
        _wait_is_over()

        issue_held_acs_vouchers.run()

        assert acs.orders_requested().count(str(waiting.order_id)) == 0

    def test_a_store_without_acs_is_skipped(self, acs):
        with patch("shipping_acs.config.is_configured", return_value=False):
            assert issue_held_acs_vouchers.run() == {
                "status": "skipped_unconfigured"
            }

    def test_the_schedule_runs_it_for_every_store(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE["issue-held-acs-vouchers"]

        assert entry["task"] == "tenant.tasks.fanout_issue_held_acs_vouchers"
        with patch("tenant.tasks.run_for_all_tenants") as fan_out:
            from tenant.tasks import fanout_issue_held_acs_vouchers

            fanout_issue_held_acs_vouchers.run()
        fan_out.assert_called_once_with(issue_held_acs_vouchers.name)


class TestStaffSayAcsAnswersAgain:
    @pytest.fixture
    def admin(self):
        return AcsVoucherHoldAdmin(AcsVoucherHold, AdminSite())

    @staticmethod
    def _post(user):
        request = RequestFactory().post("/", {"_form_submitted": "on"})
        request.user = user
        return request

    @pytest.fixture
    def staff(self):
        return get_user_model().objects.create_user(
            username="operator", email="operator@example.com", password="x"
        )

    def test_it_lets_one_request_go_without_the_wait(
        self, acs, sent, admin, staff
    ):
        _lose_one_answer(acs)
        waiting = _shipment()
        create_acs_voucher_for_order.run(waiting.order_id)
        hold = _hold()

        with (
            patch.object(admin, "has_change_permission", return_value=True),
            patch.object(admin, "message_user"),
        ):
            response = admin.allow_one_request(
                self._post(staff), object_id=str(hold.pk)
            )

        assert response.status_code == 200
        assert response.headers["HX-Redirect"].endswith(f"/{hold.pk}/change/")
        # No read first: staff are the evidence. One request went, ACS
        # answered it, the hold is over.
        assert acs.aliases() == [CREATE, CREATE]
        waiting.refresh_from_db()
        assert waiting.voucher_no
        hold.refresh_from_db()
        assert hold.ended_at is not None
        entry = LogEntry.objects.get(object_id=str(hold.pk))
        assert entry.user_id == staff.pk
        assert "one voucher request" in entry.get_change_message()

    def test_it_asks_before_it_does_anything(self, acs, sent, admin, staff):
        _lose_one_answer(acs)
        hold = _hold()
        request = RequestFactory().get("/")
        request.user = staff

        with patch.object(admin, "has_change_permission", return_value=True):
            response = admin.allow_one_request(request, object_id=str(hold.pk))

        assert response.status_code == 200
        hold.refresh_from_db()
        assert hold.trial_allowed_at is None
        assert not LogEntry.objects.exists()

    def test_it_needs_the_change_permission(self, acs, sent, admin, staff):
        _lose_one_answer(acs)
        hold = _hold()

        with (
            patch.object(admin, "has_change_permission", return_value=False),
            pytest.raises(PermissionDenied),
        ):
            admin.allow_one_request(self._post(staff), object_id=str(hold.pk))

        hold.refresh_from_db()
        assert hold.trial_allowed_at is None

    def test_a_hold_that_already_ended_is_left_alone(
        self, acs, sent, admin, staff
    ):
        _lose_one_answer(acs)
        hold = _hold()
        AcsVoucherHold.objects.filter(pk=hold.pk).update(
            ended_at=timezone.now()
        )

        with (
            patch.object(admin, "has_change_permission", return_value=True),
            patch.object(admin, "message_user") as told,
        ):
            admin.allow_one_request(self._post(staff), object_id=str(hold.pk))

        assert "no longer" in str(told.call_args.args[1])
        assert not LogEntry.objects.exists()

    def test_the_list_says_which_state_a_hold_is_in(self, acs, sent, admin):
        _lose_one_answer(acs)
        assert admin.state(_hold()) == "Held: nothing is sent"

        waiting = _shipment()
        create_acs_voucher_for_order.run(waiting.order_id)
        assert voucher_hold.allow_trial()
        assert admin.state(_hold()) == "One request first"

        AcsShipment.objects.filter(pk=waiting.pk).update(
            voucher_request_held_at=None
        )
        assert (
            admin.state(_hold()) == "No request waits: the next one goes alone"
        )

    def test_holds_are_not_added_edited_or_deleted_by_hand(self, admin, staff):
        request = RequestFactory().get("/")
        request.user = staff

        assert not admin.has_add_permission(request)
        assert not admin.has_delete_permission(request)
        editable = set(admin.get_fields(request)) - set(
            admin.get_readonly_fields(request)
        )
        assert not editable
