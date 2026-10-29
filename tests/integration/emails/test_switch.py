"""Switching an email type off and on: who may, what is refused, and that
the send path follows at once."""

from __future__ import annotations

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError

from emails.models import (
    EMAIL_TYPE_REQUIRED,
    UNKNOWN_EMAIL_TYPE,
    EmailSwitch,
)
from emails.send import Status, send_email, switched_off_keys
from emails.services import set_switch
from tests.utils.emails import probe, probe_object
from tests.utils.staff import store_staff
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(tenant):
    return store_staff(tenant, is_superuser=True)


@pytest.fixture(autouse=True)
def _outbox(tenant):
    mail.outbox.clear()


class TestSetSwitch:
    def test_switching_off_adds_a_row_naming_the_staff_member(
        self, catalogue, staff
    ):
        catalogue(probe())

        set_switch(staff, "probe.notice", enabled=False)

        row = EmailSwitch.objects.get(type_key="probe.notice")
        assert row.changed_by_staff_id == staff.pk

    def test_switching_on_removes_the_row(self, catalogue, staff):
        catalogue(probe())
        set_switch(staff, "probe.notice", enabled=False)

        set_switch(staff, "probe.notice", enabled=True)

        assert not EmailSwitch.objects.exists()

    def test_repeating_a_switch_changes_nothing(self, catalogue, staff):
        catalogue(probe())

        set_switch(staff, "probe.notice", enabled=True)
        set_switch(staff, "probe.notice", enabled=False)
        set_switch(staff, "probe.notice", enabled=False)

        assert EmailSwitch.objects.count() == 1
        assert EmailSwitch.history.filter(type_key="probe.notice").count() == 1

    def test_both_changes_are_in_the_history_with_their_actor(
        self, catalogue, staff
    ):
        catalogue(probe())

        set_switch(staff, "probe.notice", enabled=False)
        set_switch(staff, "probe.notice", enabled=True)

        added, removed = EmailSwitch.history.order_by("history_id")
        assert (added.history_type, removed.history_type) == ("+", "-")
        assert added.history_user_id == removed.history_user_id == staff.pk

    def test_a_required_type_cannot_be_switched_off(self, catalogue, staff):
        catalogue(probe(required=True))

        with pytest.raises(ValidationError) as raised:
            set_switch(staff, "probe.notice", enabled=False)

        assert (
            raised.value.error_dict["type_key"][0].code == EMAIL_TYPE_REQUIRED
        )
        assert not EmailSwitch.objects.exists()

    def test_switching_a_required_type_on_is_a_no_op(self, catalogue, staff):
        catalogue(probe(required=True))

        set_switch(staff, "probe.notice", enabled=True)

        assert not EmailSwitch.objects.exists()

    def test_an_unknown_type_is_refused(self, catalogue, staff):
        catalogue(probe())

        with pytest.raises(ValidationError) as raised:
            set_switch(staff, "probe.missing", enabled=False)

        assert raised.value.error_dict["type_key"][0].code == UNKNOWN_EMAIL_TYPE
        assert not EmailSwitch.objects.exists()

    def test_a_user_without_the_permission_is_refused(self, catalogue):
        catalogue(probe())
        customer = UserAccountFactory(num_addresses=0)

        with pytest.raises(PermissionDenied):
            set_switch(customer, "probe.notice", enabled=False)

        assert not EmailSwitch.objects.exists()


class TestTheSendPathFollowsTheSwitch:
    def test_off_then_on_takes_effect_through_the_cache(
        self, catalogue, staff, tenant
    ):
        """The switch list is cached per cache generation: a warm cache must
        not outlive the change that moved the generation. The next request
        (or task) reads the store row again, which ``refresh_from_db``
        stands for."""
        catalogue(probe())
        assert (
            send_email("probe.notice", probe_object())[0].status is Status.SENT
        )
        assert switched_off_keys() == frozenset()  # warm

        set_switch(staff, "probe.notice", enabled=False)
        tenant.refresh_from_db()

        [delivery] = send_email("probe.notice", probe_object())
        assert delivery.status is Status.SKIPPED_SWITCHED_OFF

        set_switch(staff, "probe.notice", enabled=True)
        tenant.refresh_from_db()

        [delivery] = send_email("probe.notice", probe_object())
        assert delivery.status is Status.SENT
        assert len(mail.outbox) == 2

    def test_a_switch_moves_the_stores_cache_generation(
        self, catalogue, staff, tenant
    ):
        catalogue(probe())
        before = type(tenant).objects.get(pk=tenant.pk).cache_generation

        set_switch(staff, "probe.notice", enabled=False)

        assert type(tenant).objects.get(pk=tenant.pk).cache_generation > before
