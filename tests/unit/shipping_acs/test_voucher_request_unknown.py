"""A voucher request whose outcome is unknown is never sent again (#622).

ACS's manual gives ``ACS_Create_Voucher`` no idempotency key and no
duplicate error, and no call finds a voucher by our reference before it is
on a pickup list (April 2026 edition, p. 7-9 and 21-22). A create whose
answer was lost (read timeout, a connection dropped after the request, a
5xx, an unreadable 200, a worker that died before saving) may have minted
a voucher, and a second create mints another one (stage order 682,
2026-04-29). So such a request becomes a durable state on the shipment
that no caller sends through: not the task's retries, not a re-dispatch,
not "Issue ACS voucher now". Staff resolve it: adopt the voucher ACS holds,
or state that there is none and request again.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from order.models.history import OrderHistory
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import (
    AcsAPIError,
    AcsOutcomeUnknownError,
    AcsRetryableError,
    AcsVoucherRequestUnknownError,
    AcsVoucherResolutionError,
)
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment, AcsUnrecognisedVoucher
from shipping_acs.models.unrecognised_voucher import (
    AcsUnrecognisedVoucherResolution,
)
from shipping_acs.services import AcsService
from shipping_acs.tasks import create_acs_voucher_for_order
from tests.utils.orders import courier_cash_order

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

V1 = "7401573174"
V2 = "7401573175"


def _lost_answer() -> AcsOutcomeUnknownError:
    return AcsOutcomeUnknownError(
        alias="ACS_Create_Voucher",
        error_message="ACS timeout after 15s: Read timed out.",
    )


class _Acs:
    """ACS as the traced sequence saw it: the first create is executed
    (V1) but its answer is lost; any later create would mint V2."""

    billing_code = "TEST_BILLING"
    creates: list[dict] = []
    first_error: Exception | None = None
    summaries: dict[str, dict] = {}
    deleted: list[str] = []

    def create_voucher(self, params):
        _Acs.creates.append(params)
        if len(_Acs.creates) == 1 and _Acs.first_error is not None:
            raise _Acs.first_error
        return {"Voucher_No": V2, "Error_Message": ""}

    def get_multipart_vouchers(self, voucher_no):
        return []

    def tracking_summary(self, voucher_no):
        return _Acs.summaries.get(voucher_no, {})

    def delete_voucher(self, voucher_no):
        _Acs.deleted.append(voucher_no)


@pytest.fixture
def acs(monkeypatch):
    from shipping_acs import services

    _Acs.creates = []
    _Acs.first_error = _lost_answer()
    _Acs.summaries = {}
    _Acs.deleted = []
    monkeypatch.setattr(services, "AcsClient", _Acs)
    return _Acs


@pytest.fixture
def sent():
    """Every operational mail the ACS service sends, by type."""
    with patch("shipping_acs.services.send_email") as send_email:
        yield send_email


def _types(sent) -> list[str]:
    return [call.args[0] for call in sent.call_args_list]


def _shipment(**kwargs):
    return AcsShipmentFactory(
        order=courier_cash_order(),
        voucher_no=None,
        shipment_state=AcsShipmentState.PENDING_CREATION,
        **kwargs,
    )


def _run_task_with_retries(order_id: int) -> dict | None:
    """The task as Celery runs it: re-run on every retryable error, up to
    ``max_retries`` times."""
    result = None
    for _attempt in range(create_acs_voucher_for_order.max_retries + 1):
        try:
            result = create_acs_voucher_for_order.run(order_id)
        except AcsRetryableError:
            continue
        break
    return result


def _notes(order_id: int) -> list[str]:
    """The order's notes about ACS vouchers (checkout writes others)."""
    return [
        h.new_value["note"]
        for h in OrderHistory.objects.filter(
            order_id=order_id, change_type="NOTE"
        )
        if "voucher" in h.new_value["note"]
    ]


class TestTheTracedSequence:
    """POST sent, read timeout, ACS created V1 (issue #622, section 1)."""

    def test_one_create_across_retries_redispatch_and_the_admin_button(
        self, acs, sent
    ):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        from shipping_acs.admin import AcsShipmentAdmin

        shipment = _shipment()
        order_id = shipment.order_id

        first = _run_task_with_retries(order_id)
        again = _run_task_with_retries(order_id)

        request = RequestFactory().get("/")
        request.user = get_user_model()(id=1, username="staff")
        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ) as dispatched,
            patch("django.contrib.messages.error") as refused,
        ):
            AcsShipmentAdmin(AcsShipment, AdminSite()).issue_voucher_now(
                request, shipment.pk
            )

        assert len(acs.creates) == 1
        assert first["status"] == "voucher_request_unknown"
        assert again["status"] == "voucher_request_unknown"
        dispatched.assert_not_called()
        refused.assert_called_once()
        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        assert shipment.voucher_no is None
        assert "mint_started_at" not in shipment.metadata
        assert "Read timed out" in shipment.metadata["last_error"]["error"]
        assert _types(sent) == ["ops.acs_voucher_request_unknown"]
        assert len(_notes(order_id)) == 1

    def test_the_task_never_reaches_the_rejection_alert(self, acs, sent):
        shipment = _shipment()

        create_acs_voucher_for_order.run(shipment.order_id)

        assert "ops.shipment_rejected" not in _types(sent)

    @pytest.mark.parametrize(
        "lost",
        [
            pytest.param(_lost_answer(), id="read-timeout"),
            pytest.param(
                AcsOutcomeUnknownError(
                    alias="ACS_Create_Voucher",
                    error_message="ACS server error 502",
                    http_status=502,
                ),
                id="5xx",
            ),
            pytest.param(
                RuntimeError("body could not be decoded"),
                id="an-error-the-client-does-not-classify",
            ),
        ],
    )
    def test_every_lost_answer_leads_to_the_same_state(self, acs, sent, lost):
        acs.first_error = lost
        shipment = _shipment()

        with pytest.raises(AcsVoucherRequestUnknownError):
            AcsService.create_voucher_for_order(shipment.order)

        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        assert "mint_started_at" not in shipment.metadata

    def test_a_success_answer_without_a_voucher_or_a_message_is_unknown(
        self, acs, sent
    ):
        acs.first_error = None
        shipment = _shipment()

        with patch.object(
            _Acs,
            "create_voucher",
            lambda self, params: _Acs.creates.append(params) or {},
        ):
            with pytest.raises(AcsVoucherRequestUnknownError):
                AcsService.create_voucher_for_order(shipment.order)

        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None


class TestKnownOutcomes:
    def test_a_request_that_never_reached_acs_is_sent_again(self, acs, sent):
        acs.first_error = AcsRetryableError(
            alias="ACS_Create_Voucher",
            error_message="ACS connection error: Failed to establish",
        )
        shipment = _shipment()

        result = _run_task_with_retries(shipment.order_id)

        assert result["status"] == "ok"
        assert len(acs.creates) == 2
        shipment.refresh_from_db()
        assert shipment.voucher_no == V2
        assert shipment.voucher_request_unknown_at is None
        assert sent.call_count == 0

    def test_an_acs_refusal_releases_the_claim_and_alerts_as_before(
        self, acs, sent
    ):
        acs.first_error = AcsAPIError(
            alias="ACS_Create_Voucher",
            error_message="Μη αποδεκτός ταχ. Κωδικός",
            raw={"ACSExecution_HasError": True},
        )
        shipment = _shipment()

        result = create_acs_voucher_for_order.run(shipment.order_id)

        assert result["status"] == "acs_api_error"
        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is None
        assert "mint_started_at" not in shipment.metadata
        # The rejection alert (``shipping.alerts``), not the check alert.
        assert sent.call_count == 0
        assert any(
            "shipment creation failed" in h.new_value["note"]
            for h in OrderHistory.objects.filter(
                order_id=shipment.order_id, change_type="NOTE"
            )
        )


class TestACrashBeforeTheSave:
    """A claim older than its TTL with no voucher: the worker died
    between the POST and the save, so ACS may hold a voucher."""

    def test_an_expired_claim_is_never_minted_again(self, acs, sent):
        stale = timezone.now() - timedelta(
            seconds=AcsService._MINT_CLAIM_TTL_SECONDS + 1
        )
        shipment = _shipment(metadata={"mint_started_at": stale.isoformat()})

        result = create_acs_voucher_for_order.run(shipment.order_id)

        assert result["status"] == "voucher_request_unknown"
        assert acs.creates == []
        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        assert "mint_started_at" not in shipment.metadata
        assert _types(sent) == ["ops.acs_voucher_request_unknown"]

    def test_a_claim_inside_its_ttl_still_waits(self, acs, sent):
        fresh = timezone.now() - timedelta(seconds=5)
        shipment = _shipment(metadata={"mint_started_at": fresh.isoformat()})

        with pytest.raises(AcsRetryableError):
            AcsService.create_voucher_for_order(shipment.order)

        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is None
        assert acs.creates == []


def _unknown_shipment(acs):
    shipment = _shipment()
    with pytest.raises(AcsVoucherRequestUnknownError):
        AcsService.create_voucher_for_order(shipment.order)
    shipment.refresh_from_db()
    return shipment


class TestAdoptTheVoucherAcsHolds:
    def test_adopting_saves_the_voucher_without_a_second_create(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {"voucher_no": V1, "recipient": "ΠΑΠΑΔΟΠΟΥΛΟΣ"}

        AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert len(acs.creates) == 1
        assert shipment.voucher_no == V1
        assert shipment.shipment_state == AcsShipmentState.NEW
        assert shipment.voucher_request_unknown_at is None
        assert shipment.order.tracking_number == V1
        assert any(V1 in note for note in _notes(shipment.order_id))

    def test_a_voucher_acs_named_on_a_refused_pickup_list_is_adoptable(
        self, acs, sent
    ):
        """ACS's own statement that it holds the voucher for our account
        (``ACS_Issue_Pickup_List``, p. 14), even where its tracking
        summary has nothing for a voucher that is not on a list yet."""
        shipment = _unknown_shipment(acs)
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

        AcsService.adopt_voucher(shipment, V1, user=None)

        row = AcsUnrecognisedVoucher.objects.get(voucher_no=V1)
        assert row.resolution == AcsUnrecognisedVoucherResolution.ADOPTED
        assert row.resolved_at is not None
        assert row.shipment_id == shipment.pk

    def test_a_voucher_acs_does_not_know_is_refused(self, acs, sent):
        shipment = _unknown_shipment(acs)

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert shipment.voucher_no is None
        assert shipment.voucher_request_unknown_at is not None

    def test_a_voucher_another_shipment_carries_is_refused(self, acs, sent):
        AcsShipmentFactory(voucher_no=V1)
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {"voucher_no": V1}

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

    def test_only_a_request_of_unknown_outcome_can_adopt(self, acs, sent):
        shipment = _shipment()
        acs.summaries[V1] = {"voucher_no": V1}

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)


class TestRequestAgain:
    def test_staff_who_found_no_voucher_get_exactly_one_new_request(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)

        with patch(
            "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
        ) as dispatched:
            AcsService.request_voucher_again(
                shipment, user=None, note="Checked the ACS portal: none."
            )
        create_acs_voucher_for_order.run(shipment.order_id)

        dispatched.assert_called_once()
        shipment.refresh_from_db()
        assert len(acs.creates) == 2
        assert shipment.voucher_no == V2
        assert shipment.voucher_request_unknown_at is None
        assert any(
            "Checked the ACS portal" in note
            for note in _notes(shipment.order_id)
        )

    def test_a_shipment_without_an_unknown_request_is_refused(self, acs, sent):
        shipment = _shipment()

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.request_voucher_again(shipment, user=None, note="x")
