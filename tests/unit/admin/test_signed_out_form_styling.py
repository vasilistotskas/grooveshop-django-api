"""Every input on the signed-out admin pages carries Unfold's input styling.

Django's own widgets render a bare ``<input>``, which Unfold's CSS draws with
no border: the field is there but cannot be seen. That shipped twice, on the
sign-in page and on the password reset pages.
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.cache import SessionStore
from django.test import RequestFactory
from unfold.widgets import BASE_INPUT_CLASSES

from admin.forms import PlatformAdminAuthenticationForm
from admin.password_reset import StaffPasswordResetForm, StaffSetPasswordForm
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _styled(form, names) -> dict[str, bool]:
    return {
        name: set(BASE_INPUT_CLASSES)
        <= set(form.fields[name].widget.attrs.get("class", "").split())
        for name in names
    }


def test_the_sign_in_inputs():
    request = RequestFactory().get("/admin/login/")
    request.session = SessionStore()
    form = PlatformAdminAuthenticationForm(request=request)

    assert _styled(form, ("username", "password")) == {
        "username": True,
        "password": True,
    }


def test_the_reset_request_input():
    assert _styled(StaffPasswordResetForm(), ("email",)) == {"email": True}


def test_the_new_password_inputs():
    form = StaffSetPasswordForm(UserAccountFactory())

    assert _styled(form, ("new_password1", "new_password2")) == {
        "new_password1": True,
        "new_password2": True,
    }
