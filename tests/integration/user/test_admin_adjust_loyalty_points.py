"""The loyalty adjustment is a dialog form, and only a platform
superuser gets it."""

from __future__ import annotations

import pytest
from django.contrib.admin import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from tests.utils.staff import stamp_platform_identity
from user.admin import UserAdmin
from user.factories.account import UserAccountFactory
from user.models import UserAccount

pytestmark = pytest.mark.django_db


def _post(user, **data):
    request = RequestFactory().post("/", data={**data, "_form_submitted": "on"})
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def user_admin():
    return UserAdmin(UserAccount, AdminSite())


@pytest.fixture
def superuser():
    return stamp_platform_identity(UserAccountFactory(admin=True))


def test_it_writes_one_ledger_row_and_redirects(user_admin, superuser):
    customer = UserAccountFactory()

    response = user_admin.adjust_loyalty_points(
        _post(superuser, points=-40, reason="Refund"), object_id=customer.pk
    )

    row = PointsTransaction.objects.get(user=customer)
    assert row.points == -40
    assert row.transaction_type == TransactionType.ADJUST
    assert row.description == "Refund"
    assert response["HX-Redirect"].endswith(
        f"/user/useraccount/{customer.pk}/change/"
    )


def test_an_out_of_range_amount_writes_nothing(user_admin, superuser):
    customer = UserAccountFactory()

    user_admin.adjust_loyalty_points(
        _post(superuser, points=10001, reason="x"), object_id=customer.pk
    )

    assert not PointsTransaction.objects.filter(user=customer).exists()


def test_store_staff_is_refused(user_admin):
    staff = stamp_platform_identity(UserAccountFactory(is_staff=True))
    customer = UserAccountFactory()

    assert not user_admin.has_adjust_loyalty_points_permission(_post(staff))
    with pytest.raises(PermissionDenied):
        user_admin.adjust_loyalty_points(
            _post(staff, points=5, reason="x"), object_id=customer.pk
        )
