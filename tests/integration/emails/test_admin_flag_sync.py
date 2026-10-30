"""Staff may record a customer's marketing objection, never lift it.

The account's ``marketing_opted_out_at`` is state; ``EmailOptOut`` is what
stops the mail. Setting the field in the customer admin writes the row; once
set the field is read-only, so no save can clear it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.utils import timezone

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from user.admin import UserAdmin
from user.factories.account import UserAccountFactory
from user.models import UserAccount

pytestmark = pytest.mark.django_db

FIELD = "marketing_opted_out_at"


def model_admin() -> UserAdmin:
    return UserAdmin(UserAccount, admin.site)


def request_by_superuser():
    return SimpleNamespace(user=SimpleNamespace(is_superuser=True))


def save_with_flag(user, value):
    user.marketing_opted_out_at = value
    form = SimpleNamespace(changed_data=[FIELD])
    model_admin().save_model(None, user, form, True)


def test_setting_the_field_writes_the_row_for_every_marketing_type():
    user = UserAccountFactory(num_addresses=0)

    save_with_flag(user, timezone.now())

    row = EmailOptOut.objects.get()
    assert row.address_hash == optout.address_hash(user.email)
    assert row.type_key == EVERY_MARKETING_TYPE
    assert row.source == EmailOptOut.Source.STAFF


def test_an_unset_field_is_editable():
    user = UserAccountFactory(num_addresses=0)

    readonly = model_admin().get_readonly_fields(request_by_superuser(), user)

    assert FIELD not in readonly


def test_a_set_field_is_read_only_even_for_a_superuser():
    user = UserAccountFactory(
        num_addresses=0, marketing_opted_out_at=timezone.now()
    )

    readonly = model_admin().get_readonly_fields(request_by_superuser(), user)

    assert FIELD in readonly


def test_a_save_that_cannot_change_the_field_keeps_the_row():
    """A read-only field is not in the form, so a posted cleared value is
    never in ``changed_data`` and the row stays."""
    user = UserAccountFactory(
        num_addresses=0, marketing_opted_out_at=timezone.now()
    )
    optout.opt_out(
        user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
    )

    form = SimpleNamespace(changed_data=["first_name"])
    model_admin().save_model(None, user, form, True)

    assert EmailOptOut.objects.count() == 1


class TestTheOneWriter:
    def test_it_sets_the_row_and_the_account_flag_together(self):
        from user.services.subscription import opt_out_of_marketing

        user = UserAccountFactory(num_addresses=0)

        recorded = opt_out_of_marketing(
            user.email,
            account_pk=user.pk,
            source=EmailOptOut.Source.LINK,
        )

        assert recorded is True
        user.refresh_from_db()
        assert user.marketing_opted_out_at is not None
        assert EmailOptOut.objects.get().type_key == EVERY_MARKETING_TYPE

    def test_a_guest_has_a_row_and_no_account(self):
        from user.services.subscription import opt_out_of_marketing

        recorded = opt_out_of_marketing(
            "guest@example.test",
            account_pk=None,
            source=EmailOptOut.Source.LINK,
        )

        assert recorded is False
        assert EmailOptOut.objects.get().address_hash == optout.address_hash(
            "guest@example.test"
        )

    def test_the_second_call_keeps_the_first_time(self):
        from user.services.subscription import opt_out_of_marketing

        user = UserAccountFactory(num_addresses=0)
        opt_out_of_marketing(
            user.email, account_pk=user.pk, source=EmailOptOut.Source.LINK
        )
        user.refresh_from_db()
        first = user.marketing_opted_out_at

        again = opt_out_of_marketing(
            user.email, account_pk=user.pk, source=EmailOptOut.Source.LINK
        )

        assert again is False
        user.refresh_from_db()
        assert user.marketing_opted_out_at == first
        assert EmailOptOut.objects.count() == 1
