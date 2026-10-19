"""A store always keeps at least one active owner.

``TenantMembershipRole`` promises it. The rule lives on
``UserTenantMembership`` so every path honours it: a single save or
delete, the admin form, and the bulk writes (``QuerySet.delete()`` /
``update()``) that never call the model's own methods.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils.translation import gettext

from tenant.models import (
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

OWNER = TenantMembershipRole.OWNER
ADMIN = TenantMembershipRole.ADMIN


@pytest.fixture
def tenant():
    return store_tenant("last_owner_tenant")


@pytest.fixture
def other_tenant():
    return store_tenant("last_owner_other")


def _seat(tenant, role=OWNER, *, is_active=True):
    return UserTenantMembership.objects.create(
        user=UserAccountFactory(is_staff=True),
        tenant=tenant,
        role=role,
        is_active=is_active,
    )


class TestSingleRow:
    def test_the_last_owner_cannot_be_demoted(self, tenant):
        owner = _seat(tenant)
        owner.role = ADMIN

        with pytest.raises(ValidationError) as caught:
            owner.save()

        assert caught.value.code == "last_owner"
        owner.refresh_from_db()
        assert owner.role == OWNER

    def test_the_last_owner_cannot_be_deactivated(self, tenant):
        owner = _seat(tenant)
        owner.is_active = False

        with pytest.raises(ValidationError):
            owner.save()

        owner.refresh_from_db()
        assert owner.is_active

    def test_the_last_owner_cannot_be_moved_to_another_store(
        self, tenant, other_tenant
    ):
        owner = _seat(tenant)
        owner.tenant = other_tenant

        with pytest.raises(ValidationError):
            owner.save()

    def test_the_last_owner_cannot_be_deleted(self, tenant):
        owner = _seat(tenant)

        with pytest.raises(ValidationError):
            owner.delete()

        assert UserTenantMembership.objects.filter(pk=owner.pk).exists()

    def test_an_owner_can_leave_while_another_remains(self, tenant):
        first, second = _seat(tenant), _seat(tenant)

        first.role = ADMIN
        first.save()
        assert second.is_last_active_owner

        with pytest.raises(ValidationError):
            second.delete()

    def test_an_inactive_owner_does_not_count(self, tenant):
        owner = _seat(tenant)
        _seat(tenant, is_active=False)

        with pytest.raises(ValidationError):
            owner.delete()

    def test_other_roles_change_freely(self, tenant):
        _seat(tenant)
        staff = _seat(tenant, TenantMembershipRole.STAFF)

        staff.role = ADMIN
        staff.is_active = False
        staff.save()
        staff.delete()

    def test_a_store_without_an_owner_can_still_be_edited(self, tenant):
        admin = _seat(tenant, ADMIN)

        admin.is_active = False
        admin.save()
        admin.delete()

    def test_another_stores_owner_does_not_count(self, tenant, other_tenant):
        owner = _seat(tenant)
        _seat(other_tenant)

        with pytest.raises(ValidationError):
            owner.delete()

    def test_a_new_owner_can_be_added_and_saved_again(self, tenant):
        owner = _seat(tenant)
        owner.save()
        owner.role = OWNER
        owner.save()

    def test_clean_reports_it_for_a_form(self, tenant):
        owner = _seat(tenant)
        owner.role = ADMIN

        with pytest.raises(ValidationError) as caught:
            owner.full_clean(exclude=["user", "tenant"])

        assert caught.value.error_dict["__all__"][0].code == "last_owner"


class TestBulkWrites:
    def test_queryset_delete_refuses_to_remove_every_owner(self, tenant):
        _seat(tenant)
        _seat(tenant)

        with pytest.raises(ValidationError):
            UserTenantMembership.objects.filter(tenant=tenant).delete()

        assert UserTenantMembership.objects.filter(tenant=tenant).count() == 2

    def test_queryset_delete_may_leave_one_owner(self, tenant):
        first, _second = _seat(tenant), _seat(tenant)

        UserTenantMembership.objects.filter(pk=first.pk).delete()

        assert UserTenantMembership.objects.filter(tenant=tenant).count() == 1

    def test_queryset_delete_checks_each_store(self, tenant, other_tenant):
        _seat(tenant)
        _seat(tenant)
        _seat(other_tenant)

        with pytest.raises(ValidationError):
            UserTenantMembership.objects.filter(role=OWNER).delete()

    @pytest.mark.parametrize(
        "change",
        [{"role": ADMIN}, {"is_active": False}],
        ids=["demote", "deactivate"],
    )
    def test_queryset_update_refuses_to_strip_every_owner(self, tenant, change):
        _seat(tenant)
        _seat(tenant)

        with pytest.raises(ValidationError):
            UserTenantMembership.objects.filter(tenant=tenant).update(**change)

        assert (
            UserTenantMembership.objects.filter(
                tenant=tenant, role=OWNER, is_active=True
            ).count()
            == 2
        )

    def test_queryset_update_may_leave_one_owner(self, tenant):
        first, _second = _seat(tenant), _seat(tenant)

        UserTenantMembership.objects.filter(pk=first.pk).update(role=ADMIN)

        first.refresh_from_db()
        assert first.role == ADMIN

    def test_queryset_update_of_other_columns_is_not_checked(self, tenant):
        _seat(tenant)

        UserTenantMembership.objects.filter(tenant=tenant).update(
            role=OWNER, is_active=True
        )

    def test_the_related_manager_is_guarded_too(self, tenant):
        _seat(tenant)

        with pytest.raises(ValidationError):
            tenant.user_memberships.all().delete()


class TestCascades:
    def test_deleting_a_store_still_removes_its_owner(self, tenant):
        owner = _seat(tenant)

        tenant.delete(force_drop=False)

        assert not UserTenantMembership.objects.filter(pk=owner.pk).exists()

    def test_deleting_an_identity_still_removes_its_seats(self, tenant):
        owner = _seat(tenant)

        owner.user.delete()

        assert not UserTenantMembership.objects.filter(pk=owner.pk).exists()


class TestAdmin:
    @staticmethod
    def _admin():
        return admin_site._registry[UserTenantMembership]

    @staticmethod
    def _request():
        request = RequestFactory().post("/admin/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        return request

    def test_delete_is_withheld_for_the_last_owner(self, tenant):
        owner = _seat(tenant)

        assert not self._admin().has_delete_permission(self._request(), owner)

    def test_delete_is_offered_while_another_owner_remains(self, tenant):
        owner, _second = _seat(tenant), _seat(tenant)

        assert self._admin().has_delete_permission(self._request(), owner)

    def test_bulk_delete_answers_with_a_message(self, tenant):
        from django.contrib.messages.storage.fallback import FallbackStorage

        _seat(tenant)
        _seat(tenant)
        request = self._request()
        request.session = {}
        request._messages = FallbackStorage(request)
        request.POST = request.POST.copy()
        request.POST["post"] = "yes"
        _func, _name, _desc = self._admin().get_actions(request)[
            "delete_selected"
        ]

        response = _func(
            self._admin(),
            request,
            UserTenantMembership.objects.filter(tenant=tenant),
        )

        assert response is None
        assert UserTenantMembership.objects.filter(tenant=tenant).count() == 2
        assert [str(m) for m in request._messages] == [
            gettext("A store must keep at least one active owner.")
        ]
