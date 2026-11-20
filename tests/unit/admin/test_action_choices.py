"""Unfold's ``get_action_choices`` drops Django's ``action_location``; the
project's admins restore it (``admin.mixins.ActionLocationModelAdmin``)."""

from __future__ import annotations

import warnings

import pytest
from django.contrib import admin
from django.contrib.admin.options import ActionLocation
from django.test import RequestFactory
from django.utils.translation import gettext as _

from admin.base import BaseModelAdmin
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _request():
    request = RequestFactory().get("/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    return request


def _model_admin() -> BaseModelAdmin:
    return admin.site._registry[BlogPost]


def test_choices_start_with_unfolds_blank_choice():
    request = _request()
    choices = _model_admin().get_action_choices(request)
    assert choices[0] == ("", _("Select action"))


def test_action_location_is_honoured(monkeypatch):
    seen = []
    model_admin = _model_admin()
    original = model_admin._get_actions_with_action_location

    def spy(request, action_location):
        seen.append(action_location)
        return original(request, action_location=action_location)

    monkeypatch.setattr(model_admin, "_get_actions_with_action_location", spy)
    request = _request()
    model_admin.get_action_choices(request)
    model_admin.get_action_choices(
        request, action_location=ActionLocation.CHANGE_FORM
    )
    assert seen == [ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM]


def test_no_action_location_deprecation_warning():
    request = _request()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _model_admin().get_action_choices(request)
