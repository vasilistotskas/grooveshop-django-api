"""The admin's "forgot password" flow for staff (``admin.password_reset``).

Django's own reset views, mounted on both admin sites, looking in the
PUBLIC schema where staff identities live. What is pinned here: a staff
member can get a link; the page cannot be used to learn which addresses
are accounts; it is rate limited; and a reset sets a password and nothing
else, so a platform superuser still meets the authenticator step.
"""

from __future__ import annotations

import re
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from allauth.mfa.models import Authenticator
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import reverse

from admin.forms import PlatformAdminAuthenticationForm
from tenant.models import PlatformStaffToken
from tests.utils.staff import enrol_staff_mfa
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

OLD_PASSWORD = "Old-passw0rd-of-the-staff!"
NEW_PASSWORD = "A-brand-new-passw0rd-2026!"
LINK = re.compile(r"https?://\S+/admin/reset/\S+")


@pytest.fixture(autouse=True)
def _fresh_budgets():
    cache.clear()


def _staff(email="reset-staff@example.com", **kwargs):
    kwargs.setdefault("is_staff", True)
    kwargs.setdefault("is_superuser", True)
    user = UserAccountFactory(email=email, num_addresses=0, **kwargs)
    user.set_password(OLD_PASSWORD)
    user.save()
    return user


def _ask(email, client=None, **extra):
    client = client or Client()
    return client.post(
        reverse("admin:admin_password_reset"), {"email": email}, **extra
    )


def _link() -> str:
    assert len(mail.outbox) == 1, [m.to for m in mail.outbox]
    return LINK.search(mail.outbox[0].body).group(0)


def _set_password(client, link, password=NEW_PASSWORD):
    # Django swaps the token for a session marker on the first visit, so
    # the form is posted to the redirect it answers with.
    response = client.get(link)
    assert response.status_code == 302
    return client.post(
        response["Location"],
        {"new_password1": password, "new_password2": password},
    )


def _login_form(user, password=NEW_PASSWORD, otp=""):
    request = RequestFactory().post("/admin/login/")
    request.session = {}
    form = PlatformAdminAuthenticationForm(
        request,
        data={"username": user.email, "password": password, "otp_code": otp},
    )
    form.is_valid()
    return form


class TestTheLoginPageOffersIt:
    def test_the_link_is_on_the_login_page(self):
        response = Client().get(reverse("admin:login"), follow=True)

        assert response.status_code == 200
        assert (
            reverse("admin:admin_password_reset") in response.content.decode()
        )


class TestAsking:
    def test_a_staff_member_gets_a_link_on_the_host_they_asked(self):
        user = _staff()

        response = _ask(user.email, HTTP_HOST="admin.example.test")

        assert response.status_code == 302
        assert response["Location"] == reverse(
            "admin:admin_password_reset_done"
        )
        [message] = mail.outbox
        assert message.to == [user.email]
        link = urlsplit(LINK.search(message.body).group(0))
        assert link.netloc == "admin.example.test"
        assert link.path.startswith("/admin/reset/")

    def test_the_answer_is_the_same_for_an_address_that_is_no_account(self):
        _staff()

        known = _ask("reset-staff@example.com")
        mail.outbox.clear()
        unknown = _ask("nobody@example.com")

        assert (unknown.status_code, unknown["Location"]) == (
            known.status_code,
            known["Location"],
        )
        assert mail.outbox == []

    def test_the_address_is_matched_whatever_its_case(self):
        _staff()

        _ask("Reset-Staff@EXAMPLE.com")

        assert len(mail.outbox) == 1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"is_staff": False},
            {"is_active": False},
            # Staff without the superuser flag need a role in the store;
            # this admin has no store bound, which admits superusers only.
            {"is_superuser": False},
        ],
        ids=["customer", "inactive", "not-a-superuser-here"],
    )
    def test_an_account_that_could_not_sign_in_here_is_not_mailed(self, kwargs):
        user = _staff(**kwargs)

        response = _ask(user.email)

        assert response["Location"] == reverse(
            "admin:admin_password_reset_done"
        )
        assert mail.outbox == []

    def test_an_account_with_no_usable_password_is_not_mailed(self):
        user = _staff()
        user.set_unusable_password()
        user.save()

        _ask(user.email)

        assert mail.outbox == []


class TestRateLimits:
    def test_one_address_is_mailed_a_few_times_then_silently_not(
        self, settings
    ):
        settings.STAFF_PASSWORD_RESET = {
            **settings.STAFF_PASSWORD_RESET,
            "PER_EMAIL": 2,
        }
        user = _staff()

        responses = [_ask(user.email) for _ in range(4)]

        assert len(mail.outbox) == 2
        # The spent budget is not announced: every answer is the same.
        assert {(r.status_code, r["Location"]) for r in responses} == {
            (302, reverse("admin:admin_password_reset_done"))
        }

    def test_one_caller_is_refused_after_its_budget(self, settings):
        settings.STAFF_PASSWORD_RESET = {
            **settings.STAFF_PASSWORD_RESET,
            "PER_IP": 2,
        }

        statuses = [
            _ask(
                f"nobody{n}@example.com", REMOTE_ADDR="203.0.113.7"
            ).status_code
            for n in range(3)
        ]

        assert statuses == [302, 302, 429]

    def test_another_caller_has_its_own_budget(self, settings):
        settings.STAFF_PASSWORD_RESET = {
            **settings.STAFF_PASSWORD_RESET,
            "PER_IP": 1,
        }
        _ask("a@example.com", REMOTE_ADDR="203.0.113.7")

        response = _ask("b@example.com", REMOTE_ADDR="203.0.113.8")

        assert response.status_code == 302

    def test_a_cache_that_cannot_count_closes_the_form(self):
        user = _staff()

        with patch("admin.password_reset.cache.incr", side_effect=OSError):
            response = _ask(user.email)

        assert response.status_code == 429
        assert mail.outbox == []


class TestChoosingAPassword:
    def test_the_link_sets_the_password_and_logs_nobody_in(self):
        user = _staff()
        _ask(user.email)
        client = Client()

        response = _set_password(client, _link())

        assert response["Location"] == reverse(
            "admin:admin_password_reset_complete"
        )
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD)
        assert not user.check_password(OLD_PASSWORD)
        assert "_auth_user_id" not in client.session

    def test_a_link_works_once(self):
        user = _staff()
        _ask(user.email)
        link = _link()
        _set_password(Client(), link)

        again = Client().get(link, follow=True)

        assert again.status_code == 200
        assert not again.context["validlink"]

    def test_a_link_expires(self, settings):
        user = _staff()
        _ask(user.email)
        link = _link()
        settings.PASSWORD_RESET_TIMEOUT = -1

        response = Client().get(link, follow=True)

        assert not response.context["validlink"]

    def test_a_link_for_an_account_that_can_no_longer_sign_in_is_refused(self):
        user = _staff()
        _ask(user.email)
        link = _link()
        user.is_staff = False
        user.save()

        response = Client().get(link, follow=True)

        assert not response.context["validlink"]

    def test_a_weak_password_is_refused_and_nothing_changes(self):
        user = _staff()
        _ask(user.email)
        client = Client()
        [location] = [client.get(_link())["Location"]]

        response = client.post(
            location, {"new_password1": "12345", "new_password2": "12345"}
        )

        assert response.status_code == 200
        assert response.context["form"].errors
        user.refresh_from_db()
        assert user.check_password(OLD_PASSWORD)

    def test_staff_api_tokens_of_the_old_password_end(self):
        user = _staff()
        PlatformStaffToken.objects.create(user)
        _ask(user.email)

        _set_password(Client(), _link())

        assert not PlatformStaffToken.objects.filter(user=user).exists()


class TestMfaIsNotBypassed:
    def test_a_superuser_with_an_authenticator_is_still_asked_for_a_code(self):
        user = _staff()
        enrol_staff_mfa(user)
        _ask(user.email)

        _set_password(Client(), _link())

        form = _login_form(user)
        assert not form.is_valid()
        assert form.mfa_stage == "code"
        assert form.get_user() is None

    def test_the_reset_leaves_the_authenticators_alone(self):
        user = _staff()
        enrol_staff_mfa(user)
        before = set(
            Authenticator.objects.filter(user_id=user.pk).values_list(
                "pk", "type"
            )
        )
        _ask(user.email)

        _set_password(Client(), _link())

        assert before
        assert before == set(
            Authenticator.objects.filter(user_id=user.pk).values_list(
                "pk", "type"
            )
        )

    def test_a_superuser_without_one_still_has_to_enrol(self):
        user = _staff()
        _ask(user.email)

        _set_password(Client(), _link())

        form = _login_form(user)
        assert not form.is_valid()
        assert form.mfa_stage == "enrol"
