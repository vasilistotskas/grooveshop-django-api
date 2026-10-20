"""The cluster-wide cap on in-flight ACS requests.

Production, 2026-10-20: 22% of tracking calls were rejected with 406,
all of them inside the poll fan-out, where six to eight calls overlapped.
These run against the real Redis cache, because the slots are Redis keys.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from celery.utils.time import get_exponential_backoff_interval
from django.core.cache import cache
from django.test import override_settings
from redis.exceptions import ConnectionError as RedisConnectionError

from shipping_acs.client import AcsClient
from shipping_acs.concurrency import acs_request_slot
from shipping_acs.exceptions import AcsAuthError, AcsRetryableError
from shipping_acs.tasks import poll_acs_tracking_batch, poll_acs_tracking_one

pytestmark = pytest.mark.django_db


@pytest.fixture
def account() -> str:
    """A fresh account per test, so slots left behind never leak across."""
    return f"company-{uuid.uuid4().hex}"


def _hold(account: str, count: int, stack) -> None:
    for _ in range(count):
        stack.enter_context(acs_request_slot(account=account, alias="A"))


class TestSlots:
    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=2, ACS_SLOT_WAIT_SECONDS=0)
    def test_cap_is_reached_then_refused_as_retryable(self, account):
        from contextlib import ExitStack

        with ExitStack() as stack:
            _hold(account, 2, stack)
            with pytest.raises(AcsRetryableError) as caught:
                with acs_request_slot(account=account, alias="ACS_X"):
                    pytest.fail("a third request must not get a slot")
        assert caught.value.alias == "ACS_X"
        assert not isinstance(caught.value, AcsAuthError)

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_slot_is_released_on_normal_exit(self, account):
        with acs_request_slot(account=account, alias="A"):
            pass
        with acs_request_slot(account=account, alias="A"):
            pass

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_slot_is_released_when_the_body_raises(self, account):
        with pytest.raises(ValueError, match="boom"):
            with acs_request_slot(account=account, alias="A"):
                raise ValueError("boom")
        with acs_request_slot(account=account, alias="A"):
            pass

    @override_settings(
        ACS_MAX_CONCURRENT_REQUESTS=1,
        ACS_SLOT_WAIT_SECONDS=0,
        ACS_SLOT_TTL_SECONDS=1,
    )
    def test_slot_of_a_dead_worker_expires(self, account):
        # Enter and never exit: the holder crashed.
        ctx = acs_request_slot(account=account, alias="A")
        ctx.__enter__()
        with pytest.raises(AcsRetryableError):
            with acs_request_slot(account=account, alias="A"):
                pass
        time.sleep(1.2)
        with acs_request_slot(account=account, alias="A"):
            pass

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_accounts_do_not_share_slots(self, account):
        other = f"{account}-other"
        with acs_request_slot(account=account, alias="A"):
            with acs_request_slot(account=other, alias="A"):
                pass

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_tenants_do_not_share_slots(self, account, bind_tenant_and_schema):
        bind_tenant_and_schema(SimpleNamespace(schema_name="store-a"))
        with acs_request_slot(account=account, alias="A"):
            bind_tenant_and_schema(SimpleNamespace(schema_name="store-b"))
            with acs_request_slot(account=account, alias="A"):
                pass

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=5)
    def test_waiter_takes_the_slot_once_it_frees(self, account):
        calls = {"n": 0}
        real_add = cache.add

        def add(*args, **kwargs):
            # Busy on the first two passes, then the holder lets go.
            calls["n"] += 1
            if calls["n"] <= 2:
                return False
            return real_add(*args, **kwargs)

        with patch.object(cache, "add", side_effect=add):
            with acs_request_slot(account=account, alias="A"):
                pass
        assert calls["n"] == 3

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=1)
    def test_wait_is_bounded(self, account):
        with acs_request_slot(account=account, alias="A"):
            started = time.monotonic()
            with pytest.raises(AcsRetryableError):
                with acs_request_slot(account=account, alias="A"):
                    pass
            waited = time.monotonic() - started
        assert 0.9 <= waited < 3

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_redis_outage_skips_the_cap_instead_of_failing(self, account):
        with patch.object(
            cache, "add", side_effect=RedisConnectionError("down")
        ):
            with acs_request_slot(account=account, alias="A") as held:
                assert held is None


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ACSOutputResponce": {}}
    return resp


class TestClientUsesTheCap:
    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    @pytest.mark.parametrize(
        ("method", "kwargs"),
        [
            ("create_voucher", {"params": {}}),
            ("delete_voucher", {"voucher_no": "V1"}),
            ("print_voucher", {"voucher_no": "V1"}),
            ("issue_pickup_list", {"pickup_date": "2026-10-21"}),
            ("get_pickup_lists", {"pickup_date": "2026-10-21"}),
            ("tracking_summary", {"voucher_no": "V1"}),
            ("tracking_details", {"voucher_no": "V1"}),
            ("stations", {}),
            ("price_calculation", {"params": {}}),
            ("cod_beneficiary_info", {}),
        ],
    )
    def test_every_request_path_is_refused_when_no_slot_is_free(
        self, account, method, kwargs
    ):
        session = MagicMock()
        session.post.return_value = _ok_response()
        client = AcsClient(
            api_key="k",
            company_id=account,
            company_password="p",
            user_id="u",
            user_password="p",
            billing_code="b",
            api_base_url="https://example/acs",
            session=session,
        )
        with acs_request_slot(account=account, alias="A"):
            with pytest.raises(AcsRetryableError):
                getattr(client, method)(**kwargs)
        session.post.assert_not_called()

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_slot_is_held_during_the_request_and_released_after(self, account):
        seen = {}

        def post(*args, **kwargs):
            with pytest.raises(AcsRetryableError):
                with acs_request_slot(account=account, alias="Inner"):
                    pass
            seen["busy_during_post"] = True
            return _ok_response()

        session = MagicMock()
        session.post.side_effect = post
        client = AcsClient(
            api_key="k",
            company_id=account,
            company_password="p",
            user_id="u",
            user_password="p",
            billing_code="b",
            api_base_url="https://example/acs",
            session=session,
        )
        client.tracking_summary("V1")
        assert seen == {"busy_during_post": True}
        with acs_request_slot(account=account, alias="A"):
            pass

    @override_settings(ACS_MAX_CONCURRENT_REQUESTS=1, ACS_SLOT_WAIT_SECONDS=0)
    def test_slot_is_released_when_acs_rejects_with_406(self, account):
        rejected = MagicMock()
        rejected.status_code = 406
        session = MagicMock()
        session.post.return_value = rejected
        client = AcsClient(
            api_key="k",
            company_id=account,
            company_password="p",
            user_id="u",
            user_password="p",
            billing_code="b",
            api_base_url="https://example/acs",
            session=session,
        )
        with pytest.raises(AcsAuthError):
            client.tracking_summary("V1")
        with acs_request_slot(account=account, alias="A"):
            pass


class TestPollTasks:
    def test_rejected_poll_retries_after_a_real_delay(
        self, acs_configured_tenant
    ):
        task = poll_acs_tracking_one
        shipment = MagicMock(id=1)
        with (
            patch("shipping_acs.models.AcsShipment.objects") as objects,
            patch(
                "shipping_acs.services.AcsService.poll_shipment_tracking",
                side_effect=AcsAuthError(alias="ACS_Trackingsummary"),
            ),
            patch.object(task, "retry", side_effect=Retry()) as retry,
        ):
            objects.select_related.return_value.get.return_value = shipment
            with pytest.raises(Retry):
                task.run(1)
        assert retry.call_args.kwargs["countdown"] == 10

    def test_backoff_never_starts_near_zero_and_doubles(self):
        task = poll_acs_tracking_one
        delays = [
            get_exponential_backoff_interval(
                task.retry_backoff,
                retries,
                task.retry_backoff_max,
                task.retry_jitter,
            )
            for retries in range(task.max_retries)
        ]
        assert delays == [10, 20, 40, 80, 160]

    @override_settings(ACS_POLL_DISPATCH_SPACING_SECONDS=0.5)
    def test_batch_spaces_dispatch_by_the_setting(self, acs_configured_tenant):
        with (
            patch("shipping_acs.models.AcsShipment.objects") as objects,
            patch(
                "shipping_acs.tasks.poll_acs_tracking_one.apply_async"
            ) as dispatch,
        ):
            chain = objects.real.return_value.filter.return_value
            chain = chain.exclude.return_value.filter.return_value
            chain.order_by.return_value.values_list.return_value.__getitem__.return_value = [
                11,
                12,
                13,
            ]
            result = poll_acs_tracking_batch.run()
        assert result == {"dispatched": 3}
        assert [c.kwargs["countdown"] for c in dispatch.call_args_list] == [
            0.0,
            0.5,
            1.0,
        ]
