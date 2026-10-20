"""Tests for the store-side team form of ``UserTenantMembershipAdmin``.

Staff identities are shared across every store, so a store's Admin never
gets a chooser of accounts: the person is named by their exact email
(``StoreTeamMemberForm``) and the store is always the one being served.
The cross-schema behaviour on a real store host is covered by
``tests_mt/test_store_admin_shows_only_its_own_people_and_no_secrets.py``.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.admin import site as admin_site
from django.test import RequestFactory
from django.utils.translation import gettext

from tenant.admin import StoreTeamMemberForm, UserTenantMembershipAdmin
from tenant.models import TenantMembershipRole, UserTenantMembership
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

ADD = "/admin/tenant/usertenantmembership/add/"


@pytest.fixture
def tenant():
    return store_tenant("team_form_tenant")


def _admin():
    return UserTenantMembershipAdmin(UserTenantMembership, admin_site)


def _form(data, tenant):
    with mock.patch("tenant.admin.bound_tenant", return_value=tenant):
        form = StoreTeamMemberForm(data)
        form.is_valid()
    return form


class TestStoreTeamMemberForm:
    def test_an_exact_email_binds_that_account_to_this_store(self, tenant):
        person = UserAccountFactory(email="exact.person@example.com")

        form = _form(
            {
                "email": "exact.person@example.com",
                "role": TenantMembershipRole.STAFF,
                "is_active": True,
            },
            tenant,
        )

        assert form.is_valid(), form.errors
        assert form.instance.user == person
        assert form.instance.tenant == tenant

    def test_the_match_ignores_case(self, tenant):
        person = UserAccountFactory(email="mixed.case@example.com")

        form = _form(
            {
                "email": "Mixed.Case@Example.com",
                "role": TenantMembershipRole.STAFF,
            },
            tenant,
        )

        assert form.instance.user == person

    @pytest.mark.parametrize(
        "typed", ["person@example.com", "xact.person@example.com"]
    )
    def test_a_fragment_matches_nobody(self, tenant, typed):
        UserAccountFactory(email="exact.person@example.com")

        form = _form(
            {"email": typed, "role": TenantMembershipRole.STAFF}, tenant
        )

        assert not form.is_valid()
        assert form.instance.user_id is None

    def test_an_unknown_email_is_refused(self, tenant):
        form = _form(
            {
                "email": "nobody@example.com",
                "role": TenantMembershipRole.STAFF,
            },
            tenant,
        )

        assert form.errors["email"] == [
            gettext("No platform account uses this email address.")
        ]

    def test_a_person_already_on_the_team_is_refused(self, tenant):
        person = UserAccountFactory(email="already@example.com")
        UserTenantMembership.objects.create(
            user=person, tenant=tenant, role=TenantMembershipRole.STAFF
        )

        form = _form(
            {
                "email": "already@example.com",
                "role": TenantMembershipRole.ADMIN,
            },
            tenant,
        )

        assert form.errors["email"] == [
            gettext("This person is already on the team.")
        ]

    def test_a_role_must_be_chosen(self, tenant):
        UserAccountFactory(email="norole@example.com")

        form = _form({"email": "norole@example.com", "role": ""}, tenant)

        assert "role" in form.errors

    def test_the_retired_member_role_is_not_accepted(self, tenant):
        UserAccountFactory(email="retired@example.com")

        form = _form({"email": "retired@example.com", "role": "member"}, tenant)

        assert "role" in form.errors
        assert "member" not in [v for v, _ in form.fields["role"].choices]

    def test_the_form_has_no_account_or_store_chooser(self):
        assert set(StoreTeamMemberForm.base_fields) == {
            "email",
            "role",
            "is_active",
        }


class TestFormSelectionByHost:
    def _request(self):
        request = RequestFactory().get(ADD)
        request.user = UserAccountFactory(is_staff=True)
        return request

    def test_a_store_host_gets_the_email_form(self, tenant):
        with mock.patch(
            "tenant.admin.self_service_tenant", return_value=tenant
        ):
            form = _admin().get_form(self._request(), None)

        assert issubclass(form, StoreTeamMemberForm)

    def test_the_platform_host_keeps_the_account_chooser(self):
        with mock.patch("tenant.admin.self_service_tenant", return_value=None):
            form = _admin().get_form(self._request(), None)

        assert not issubclass(form, StoreTeamMemberForm)
        assert "user" in form.base_fields
        assert "tenant" in form.base_fields

    def test_a_store_host_lists_neither_stores_nor_a_search_box(self):
        with mock.patch(
            "tenant.admin.self_service_tenant", return_value=object()
        ):
            request = self._request()
            columns = _admin().get_list_display(request)
            filters = _admin().get_list_filter(request)
            search = _admin().get_search_fields(request)

        assert "tenant" not in columns
        assert "tenant" not in [entry[0] for entry in filters]
        assert search == []

    def test_the_platform_host_keeps_all_of_them(self):
        with mock.patch("tenant.admin.self_service_tenant", return_value=None):
            request = self._request()
            columns = _admin().get_list_display(request)
            filters = _admin().get_list_filter(request)
            search = _admin().get_search_fields(request)

        assert "tenant" in columns
        assert "tenant" in [entry[0] for entry in filters]
        assert search

    def test_an_existing_member_is_shown_not_chosen(self):
        with mock.patch(
            "tenant.admin.self_service_tenant", return_value=object()
        ):
            request = self._request()
            fields = _admin().get_fields(request, object())
            readonly = _admin().get_readonly_fields(request, object())

        assert fields == ["member", "role", "is_active"]
        assert "member" in readonly


class TestLoadMembers:
    def test_a_page_of_members_reads_the_accounts_in_one_query(self, tenant):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        rows = [
            UserTenantMembership.objects.create(
                user=UserAccountFactory(),
                tenant=tenant,
                role=TenantMembershipRole.STAFF,
            )
            for _ in range(4)
        ]
        fresh = list(UserTenantMembership.objects.filter(tenant=tenant))

        with CaptureQueriesContext(connection) as captured:
            UserTenantMembership.load_members(fresh)
            labels = {row.pk: str(row.member) for row in fresh}

        reads = [
            q["sql"] for q in captured if 'FROM "user_useraccount"' in q["sql"]
        ]
        assert len(reads) == 1
        assert labels == {row.pk: str(row.user) for row in rows}
