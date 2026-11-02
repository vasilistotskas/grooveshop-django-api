"""A setting the platform declares cannot be deleted from the settings admin.

Its row is how the admin, the page builder's "turn on" link and the switch
itself find it; a deleted one is only recreated by the next ``migrate``.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory
from extra_settings.models import Setting

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DECLARED = "LOYALTY_ENABLED"


def delete_allowed(obj: Setting) -> bool:
    request = RequestFactory().get("/admin/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    return site._registry[Setting].has_delete_permission(request, obj)


def test_a_declared_setting_cannot_be_deleted():
    declared, _created = Setting.objects.update_or_create(
        name=DECLARED, defaults={"value_type": "bool", "value_bool": True}
    )

    assert delete_allowed(declared) is False


def test_a_setting_nobody_declares_can_be_deleted():
    stray = Setting.objects.create(
        name="STRAY_UNDECLARED_SETTING", value_type="bool", value_bool=False
    )

    assert delete_allowed(stray) is True
