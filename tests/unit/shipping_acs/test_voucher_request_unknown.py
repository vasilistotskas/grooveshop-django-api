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

DELETE_NOTE = "Not ours: made in the ACS portal for a return."

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
    delete_error: Exception | None = None

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
        if _Acs.delete_error is not None:
            raise _Acs.delete_error
        _Acs.deleted.append(voucher_no)


@pytest.fixture
def acs(monkeypatch):
    from shipping_acs import services

    _Acs.creates = []
    _Acs.first_error = _lost_answer()
    _Acs.summaries = {}
    _Acs.deleted = []
    _Acs.delete_error = None
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
    from order.enum.status import OrderStatus

    return AcsShipmentFactory(
        # A status that needs a voucher: the factory picks one at random,
        # a canceled order included.
        order=courier_cash_order(status=OrderStatus.PENDING),
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


def _as_acs_prints_it(order) -> str:
    """The recipient as ACS's tracking summary spells it: upper case,
    without accents (manual p. 19 shows only masked upper-case values)."""
    import unicodedata

    name = f"{order.first_name} {order.last_name}".upper()
    return "".join(
        c
        for c in unicodedata.normalize("NFD", name)
        if not unicodedata.combining(c)
    )


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
        acs.summaries[V1] = {
            "voucher_no": V1,
            "recipient": _as_acs_prints_it(shipment.order),
        }

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
        summary has nothing for a voucher that is not on a list yet. It
        says nothing about WHICH order, so staff confirm by hand."""
        shipment = _unknown_shipment(acs)
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

        AcsService.adopt_voucher(
            shipment,
            V1,
            user=None,
            confirmed_by_hand="Portal shows the parcel for this order.",
        )

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


class TestAdoptConfirmsTheVoucherIsThisOrders:
    """Review of #626, finding 2."""

    def test_a_voucher_acs_reports_for_another_recipient_is_refused(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {"voucher_no": V1, "recipient": "ANOTHER PERSON"}

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert shipment.voucher_no is None

    def test_a_voucher_staff_already_deleted_at_acs_is_not_confirmed(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            resolved_at=timezone.now(),
            resolution=AcsUnrecognisedVoucherResolution.DELETED,
        )

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({"multipart_vouchers": [V1]}, id="multipart-child"),
            pytest.param({"previous_vouchers": [V1]}, id="previous-voucher"),
        ],
    )
    def test_a_number_another_shipment_carries_in_its_metadata_is_refused(
        self, acs, sent, metadata
    ):
        AcsShipmentFactory(voucher_no="9700000999", metadata=metadata)
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {
            "voucher_no": V1,
            "recipient": _as_acs_prints_it(shipment.order),
        }

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

    def test_a_concurrent_adopt_of_the_same_number_is_a_refusal_not_a_500(
        self, acs, sent, monkeypatch
    ):
        """The other adopt commits between our pre-check and our lock."""
        rival = _unknown_shipment(acs)
        acs.creates.clear()  # the next create loses its answer as well
        shipment = _unknown_shipment(acs)

        def summary_while_the_rival_saves(self, voucher_no):
            AcsShipment.objects.filter(pk=rival.pk).update(
                voucher_no=V1, voucher_request_unknown_at=None
            )
            return {
                "voucher_no": V1,
                "recipient": _as_acs_prints_it(shipment.order),
            }

        monkeypatch.setattr(
            _Acs, "tracking_summary", summary_while_the_rival_saves
        )

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert shipment.voucher_no is None


class TestALateAnswer:
    """Review of #626, finding 1: worker A waits on ACS past the claim's
    TTL, worker B finds the claim expired and records the unknown
    outcome, then A's answer arrives with the voucher."""

    def test_the_late_voucher_is_saved_and_resolves_the_unknown_outcome(
        self, acs, sent, monkeypatch
    ):
        acs.first_error = None
        shipment = _shipment()

        def answer_after_worker_b_gave_up(self, params):
            _Acs.creates.append(params)
            AcsShipment.objects.filter(pk=shipment.pk).update(
                voucher_request_unknown_at=timezone.now(), metadata={}
            )
            return {"Voucher_No": V1, "Error_Message": ""}

        monkeypatch.setattr(
            _Acs, "create_voucher", answer_after_worker_b_gave_up
        )

        AcsService.create_voucher_for_order(shipment.order)

        shipment.refresh_from_db()
        assert shipment.voucher_no == V1
        assert shipment.voucher_request_unknown_at is None
        assert "late answer" in shipment.history.first().history_change_reason
        assert any("late answer" in note for note in _notes(shipment.order_id))

    def test_saving_a_voucher_resolves_the_row_acs_named_for_it(
        self, acs, sent
    ):
        """Review finding 3: ACS named our own voucher while the mint was
        between its request and its save."""
        acs.first_error = None
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=V2,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        shipment = _shipment()

        AcsService.create_voucher_for_order(shipment.order)

        row = AcsUnrecognisedVoucher.objects.get(voucher_no=V2)
        assert row.resolution == AcsUnrecognisedVoucherResolution.ADOPTED
        assert row.shipment_id == shipment.pk


class TestResetForRemint:
    """Review of #626, finding 1: a reset never leaves a shipment in a
    state no action can leave."""

    def test_a_reset_of_a_real_voucher_clears_a_stale_flag(self, acs, sent):
        shipment = _shipment()
        AcsShipment.objects.filter(pk=shipment.pk).update(
            voucher_no=V1,
            shipment_state=AcsShipmentState.CANCELED,
            voucher_request_unknown_at=timezone.now(),
        )

        AcsService.reset_shipment_for_remint(shipment)

        shipment.refresh_from_db()
        assert shipment.voucher_no is None
        assert shipment.voucher_request_unknown_at is None

    def test_a_reset_while_the_lost_request_is_unresolved_keeps_it(
        self, acs, sent
    ):
        """Canceled before staff resolved the lost request: ACS may still
        hold that voucher, so the flag stays and both resolutions still
        work."""
        shipment = _unknown_shipment(acs)
        AcsShipment.objects.filter(pk=shipment.pk).update(
            shipment_state=AcsShipmentState.CANCELED
        )

        AcsService.reset_shipment_for_remint(shipment)

        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        acs.summaries[V1] = {
            "voucher_no": V1,
            "recipient": _as_acs_prints_it(shipment.order),
        }
        AcsService.adopt_voucher(shipment, V1, user=None)
        shipment.refresh_from_db()
        assert shipment.voucher_no == V1


class TestAdoptWhenAcsSpellsTheRecipientOtherwise:
    """Second review of #626, finding 2. The manual's tracking examples are
    redacted (p. 19: ``"recipient": "XXXXXXXXXX"``), so ACS's spelling is
    unknown: a mismatch is refused unless staff confirm it by hand."""

    def _masked(self, acs, shipment):
        acs.summaries[V1] = {"voucher_no": V1, "recipient": "ΠΑΠ*****"}

    def test_staff_can_confirm_by_hand_with_a_note(self, acs, sent):
        shipment = _unknown_shipment(acs)
        self._masked(acs, shipment)

        AcsService.adopt_voucher(
            shipment,
            V1,
            user=None,
            confirmed_by_hand="Same parcel in the ACS portal: address, COD.",
        )

        shipment.refresh_from_db()
        assert shipment.voucher_no == V1
        adopted = shipment.metadata["adopted_voucher"]
        assert adopted["confirmed_by"] == "staff"
        assert any(
            "Same parcel in the ACS portal" in note
            for note in _notes(shipment.order_id)
        )

    def test_a_blank_note_is_not_a_confirmation(self, acs, sent):
        shipment = _unknown_shipment(acs)
        self._masked(acs, shipment)

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(
                shipment, V1, user=None, confirmed_by_hand="  "
            )

    def test_a_number_acs_does_not_know_is_refused_even_by_hand(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(
                shipment, V1, user=None, confirmed_by_hand="I am sure."
            )

    def test_the_name_sent_with_the_lost_request_is_what_is_compared(
        self, acs, sent
    ):
        """The order's name may be edited between the request and the
        adopt; ACS holds the name the request carried."""
        shipment = _unknown_shipment(acs)
        sent_name = _as_acs_prints_it(shipment.order)
        order = shipment.order
        order.first_name, order.last_name = "Renamed", "Later"
        order.save(update_fields=["first_name", "last_name"])
        acs.summaries[V1] = {"voucher_no": V1, "recipient": sent_name}

        AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert shipment.voucher_no == V1


class TestOnlyAnExactNameIsAnAutomaticYes:
    """Third review of #626: every confirmation but an exact, unambiguous
    recipient match needs a written hand confirmation."""

    def _named(self):
        AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

    def test_a_number_acs_only_named_on_a_pickup_list_needs_a_hand_note(
        self, acs, sent
    ):
        """Orders A and B both wait; ACS names X (B's). Without a note,
        nothing ties X to A."""
        shipment = _unknown_shipment(acs)
        self._named()

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

        shipment.refresh_from_db()
        assert shipment.voucher_no is None

    def test_a_summary_without_a_recipient_needs_a_hand_note(self, acs, sent):
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {"voucher_no": V1}

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(shipment, V1, user=None)

    def test_a_name_another_waiting_order_shares_needs_a_hand_note(
        self, acs, sent
    ):
        """Two waiting orders for the same person: a matching name cannot
        tell which one the voucher is for."""
        shipment = _unknown_shipment(acs)
        acs.creates.clear()
        twin = _unknown_shipment(acs)
        for order in (shipment.order, twin.order):
            order.first_name, order.last_name = "Maria", "Papadopoulou"
            order.save(update_fields=["first_name", "last_name"])
        for waiting in (shipment, twin):
            metadata = waiting.metadata
            metadata["last_error"]["request_params"]["Recipient_Name"] = (
                "Maria Papadopoulou"
            )
            AcsShipment.objects.filter(pk=waiting.pk).update(metadata=metadata)
        acs.summaries[V1] = {
            "voucher_no": V1,
            "recipient": "MARIA PAPADOPOULOU",
        }

        with pytest.raises(AcsVoucherResolutionError) as exc_info:
            AcsService.adopt_voucher(shipment, V1, user=None)
        assert f"#{twin.order_id}" in str(exc_info.value)

        AcsService.adopt_voucher(
            shipment, V1, user=None, confirmed_by_hand="COD and address match."
        )
        shipment.refresh_from_db()
        assert shipment.metadata["adopted_voucher"]["confirmed_by"] == "staff"


class TestIssueVoucherNowFollowsTheRule:
    """Third review of #626: the admin button points to the action that
    can actually resolve the shipment."""

    def _press(self, shipment):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        from shipping_acs.admin import AcsShipmentAdmin

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
        dispatched.assert_not_called()
        [call] = refused.call_args_list
        return str(call.args[1])

    def test_a_retired_shipment_is_pointed_to_close(self, acs, sent):
        shipment = _unknown_shipment(acs)
        AcsShipment.objects.filter(pk=shipment.pk).update(
            shipment_state=AcsShipmentState.CANCELED
        )

        message = self._press(shipment)

        assert "No voucher needed: close" in message
        assert "Adopt" not in message

    def test_a_waiting_order_is_pointed_to_adopt_or_request_again(
        self, acs, sent
    ):
        message = self._press(_unknown_shipment(acs))

        assert "Adopt ACS voucher" in message
        assert "No voucher needed" not in message


class TestALateAnswerAfterStaffAdopted:
    """Both vouchers stay accounted for: one on the shipment, the other
    deleted at ACS or recorded as an orphan."""

    def _late_answer_after_adopt(self, acs, monkeypatch):
        acs.first_error = None
        shipment = _shipment()

        def answer_after_staff_adopted(self, params):
            _Acs.creates.append(params)
            AcsShipment.objects.filter(pk=shipment.pk).update(
                voucher_request_unknown_at=timezone.now(), metadata={}
            )
            _Acs.summaries[V1] = {
                "voucher_no": V1,
                "recipient": _as_acs_prints_it(shipment.order),
            }
            AcsService.adopt_voucher(shipment, V1, user=None)
            return {"Voucher_No": V2, "Error_Message": ""}

        monkeypatch.setattr(_Acs, "create_voucher", answer_after_staff_adopted)
        AcsService.create_voucher_for_order(shipment.order)
        shipment.refresh_from_db()
        return shipment

    def test_the_late_voucher_is_deleted_at_acs(self, acs, sent, monkeypatch):
        shipment = self._late_answer_after_adopt(acs, monkeypatch)

        assert shipment.voucher_no == V1
        assert acs.deleted == [V2]

    def test_a_late_voucher_acs_will_not_delete_is_kept_as_an_orphan(
        self, acs, sent, monkeypatch
    ):
        acs.delete_error = AcsAPIError(
            alias="ACS_Delete_Voucher", error_message="refused"
        )

        shipment = self._late_answer_after_adopt(acs, monkeypatch)

        assert shipment.voucher_no == V1
        assert shipment.metadata["orphan_vouchers"] == [V2]


class TestALateAnswerAfterStaffResolvedTheCheck:
    """Worker A's request outlives its claim; another worker records the
    outcome as unknown; staff resolve the check (request again, or close
    for a canceled order), possibly after deleting A's voucher in the ACS
    portal; then A's answer arrives. The check is over, so the answer must
    not become the shipment's voucher: it may be dead at ACS, and a
    request-again mint is about to bring the voucher the order uses."""

    def _late_answer(self, acs, monkeypatch, resolve):
        acs.first_error = None
        shipment = _shipment()

        def answer_after_staff_resolved(self, params):
            _Acs.creates.append(params)
            # Worker B found A's claim expired: unknown outcome.
            AcsShipment.objects.filter(pk=shipment.pk).update(
                voucher_request_unknown_at=timezone.now(), metadata={}
            )
            resolve(shipment)
            return {"Voucher_No": V1, "Error_Message": ""}

        monkeypatch.setattr(_Acs, "create_voucher", answer_after_staff_resolved)
        AcsService.create_voucher_for_order(shipment.order)
        shipment.refresh_from_db()
        return shipment

    def test_after_request_again_the_late_voucher_is_not_saved(
        self, acs, sent, monkeypatch
    ):
        c_claim = {}

        def request_again(shipment):
            with patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ):
                AcsService.request_voucher_again(
                    shipment, user=None, note="Nothing in the ACS portal."
                )
            # Mint C (the new request) has claimed the shipment.
            c_claim["mint_started_at"] = timezone.now().isoformat()
            AcsShipment.objects.filter(pk=shipment.pk).update(
                metadata=dict(c_claim)
            )

        shipment = self._late_answer(acs, monkeypatch, request_again)

        assert shipment.voucher_no is None
        assert acs.deleted == [V1]
        # C's claim is C's: A's answer must not release it.
        assert (
            shipment.metadata["mint_started_at"] == (c_claim["mint_started_at"])
        )

    def test_after_close_a_voucher_acs_will_not_delete_is_an_orphan(
        self, acs, sent, monkeypatch
    ):
        """Staff deleted V1 in the portal, then closed the check for the
        canceled order: ACS refuses the delete of a dead voucher."""
        from order.enum.status import OrderStatus
        from order.models.order import Order

        acs.delete_error = AcsAPIError(
            alias="ACS_Delete_Voucher", error_message="not found"
        )

        def close(shipment):
            Order.objects.filter(pk=shipment.order_id).update(
                status=OrderStatus.CANCELED
            )
            AcsService.close_voucher_request(
                shipment, user=None, note="Deleted it in the ACS portal."
            )

        shipment = self._late_answer(acs, monkeypatch, close)

        assert shipment.voucher_no is None
        assert shipment.voucher_request_unknown_at is None
        assert shipment.metadata["orphan_vouchers"] == [V1]

    def test_while_still_unknown_the_late_answer_resolves_it(
        self, acs, sent, monkeypatch
    ):
        """Unchanged: nobody resolved the check, so the answer is the
        outcome (TestALateAnswer)."""
        shipment = self._late_answer(acs, monkeypatch, lambda shipment: None)

        assert shipment.voucher_no == V1
        assert shipment.voucher_request_unknown_at is None
        assert acs.deleted == []


class TestALateWorkerAndARequestAgain:
    """Second review, finding 3: late worker A saves first and pops the
    claim of mint C (a "request again"); a pickup-list run then records
    C's voucher as unrecognised; C's save deletes it at ACS as a
    duplicate, and the row must not stay open with a dead number."""

    def test_the_duplicate_our_mint_deleted_resolves_its_row(
        self, acs, sent, monkeypatch
    ):
        acs.first_error = None
        shipment = _shipment()

        def c_answers_after_a_saved(self, params):
            _Acs.creates.append(params)
            AcsShipment.objects.filter(pk=shipment.pk).update(
                voucher_no=V1,
                shipment_state=AcsShipmentState.NEW,
                metadata={},
            )
            AcsService._raise_unrecognised_vouchers([V2], timezone.localdate())
            return {"Voucher_No": V2, "Error_Message": ""}

        monkeypatch.setattr(_Acs, "create_voucher", c_answers_after_a_saved)

        AcsService.create_voucher_for_order(shipment.order)

        assert acs.deleted == [V2]
        row = AcsUnrecognisedVoucher.objects.get(voucher_no=V2)
        assert row.resolution == AcsUnrecognisedVoucherResolution.DELETED
        assert row.resolved_at is not None


class TestAnOrderThatNoLongerNeedsAVoucher:
    """Second review, finding 1: a waiting shipment that is retired, or
    whose order is canceled, stays visible (ACS may hold a billed
    voucher) but blocks nothing and never forces a new mint."""

    @pytest.fixture(params=["retired", "order-canceled"])
    def dead(self, request, acs, sent):
        from order.enum.status import OrderStatus
        from order.models.order import Order

        shipment = _unknown_shipment(acs)
        if request.param == "retired":
            AcsShipment.objects.filter(pk=shipment.pk).update(
                shipment_state=AcsShipmentState.CANCELED
            )
        else:
            Order.objects.filter(pk=shipment.order_id).update(
                status=OrderStatus.CANCELED
            )
        shipment.refresh_from_db()
        return shipment

    def test_it_stays_counted_until_closed(self, dead):
        from shipping_acs.attention import voucher_requests_unknown

        assert list(voucher_requests_unknown()) == [dead]

        AcsService.close_voucher_request(
            dead, user=None, note="Order canceled; nothing at ACS."
        )

        dead.refresh_from_db()
        assert dead.voucher_request_unknown_at is None
        assert not voucher_requests_unknown().exists()
        assert any("nothing at ACS" in note for note in _notes(dead.order_id))

    def test_it_does_not_block_deleting_a_voucher_matching_no_order(self, dead):
        row = AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

        AcsService.delete_unrecognised_voucher(row, user=None, note=DELETE_NOTE)

        row.refresh_from_db()
        assert row.resolution == AcsUnrecognisedVoucherResolution.DELETED

    def test_it_is_never_requested_again(self, dead, acs):
        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ) as dispatched,
            pytest.raises(AcsVoucherResolutionError),
        ):
            AcsService.request_voucher_again(dead, user=None, note="x")

        dispatched.assert_not_called()

    def test_a_voucher_is_not_adopted_onto_it(self, dead, acs):
        acs.summaries[V1] = {
            "voucher_no": V1,
            "recipient": _as_acs_prints_it(dead.order),
        }

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.adopt_voucher(dead, V1, user=None)

    def test_an_order_still_waiting_cannot_be_closed(self, acs, sent):
        shipment = _unknown_shipment(acs)

        with pytest.raises(AcsVoucherResolutionError):
            AcsService.close_voucher_request(shipment, user=None, note="x")


class TestAnExpiredClaimSeenByADelete:
    """Second review, finding 4: a claim past its TTL may belong to a
    worker still waiting on ACS whose answer could be the very voucher
    being deleted. The delete records it as unknown and refuses."""

    def test_the_delete_turns_it_into_a_waiting_order_and_refuses(
        self, acs, sent
    ):
        stale = timezone.now() - timedelta(
            seconds=AcsService._MINT_CLAIM_TTL_SECONDS + 1
        )
        shipment = _shipment(metadata={"mint_started_at": stale.isoformat()})
        row = AcsUnrecognisedVoucher.objects.create(
            voucher_no=V1,
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

        with pytest.raises(AcsVoucherResolutionError) as exc_info:
            AcsService.delete_unrecognised_voucher(
                row, user=None, note=DELETE_NOTE
            )

        assert f"#{shipment.order_id}" in str(exc_info.value)
        assert acs.deleted == []
        shipment.refresh_from_db()
        assert shipment.voucher_request_unknown_at is not None
        assert _types(sent) == ["ops.acs_voucher_request_unknown"]


class TestTypedNotesReachStagingReplaced:
    """CodeRabbit on #626: a note staff type can name the recipient, so it
    is stored only where the staging anonymiser replaces it whole
    (``Kind.FREE_TEXT``, or a JSON key it reads as a note), never in a
    ``Kind.TEXT`` column, which keeps everything but e-mails, phones and
    the row's own known values."""

    TYPED = "Spoke to Maria Papadopoulou, she has the parcel"

    def test_the_resolution_note_of_a_voucher_matching_no_order(self):
        from devtools.anonymise import rules

        rule = rules.REGISTRY["shipping_acs.AcsUnrecognisedVoucher"]

        assert rule.fields["resolution_note"] is rules.Kind.FREE_TEXT

    @pytest.mark.parametrize("action", ["request_again", "close"])
    def test_a_resolution_note_stays_out_of_the_shipment_history(
        self, acs, sent, action
    ):
        from devtools.anonymise import rules
        from devtools.anonymise.redaction import key_kind

        shipment = _unknown_shipment(acs)
        if action == "close":
            AcsShipment.objects.filter(pk=shipment.pk).update(
                shipment_state=AcsShipmentState.CANCELED
            )
            AcsService.close_voucher_request(
                shipment, user=None, note=self.TYPED
            )
        else:
            with patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ):
                AcsService.request_voucher_again(
                    shipment, user=None, note=self.TYPED
                )

        history = rules.REGISTRY["shipping_acs.HistoricalAcsShipment"]
        assert history.fields["history_change_reason"] is rules.Kind.TEXT
        assert not any(
            self.TYPED in (row.history_change_reason or "")
            for row in shipment.history.all()
        )
        [note] = [
            h.new_value
            for h in OrderHistory.objects.filter(
                order_id=shipment.order_id, change_type="NOTE"
            )
            if self.TYPED in h.new_value.get("note", "")
        ]
        assert key_kind("note") is rules.Kind.FREE_TEXT
        assert set(note) == {"note"}

    def test_a_hand_confirmation_is_kept_only_in_the_order_note(
        self, acs, sent
    ):
        shipment = _unknown_shipment(acs)
        acs.summaries[V1] = {"voucher_no": V1, "recipient": "M. P."}

        AcsService.adopt_voucher(
            shipment, V1, user=None, confirmed_by_hand=self.TYPED
        )

        shipment.refresh_from_db()
        assert self.TYPED not in str(shipment.metadata)
        assert not any(
            self.TYPED in (row.history_change_reason or "")
            for row in shipment.history.all()
        )


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
