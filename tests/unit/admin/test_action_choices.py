"""Unfold's ``get_action_choices`` drops Django's ``action_location``; the
project's admins restore it (``admin.mixins.ActionLocationModelAdmin``)."""

from __future__ import annotations

import warnings

import pytest
from django.contrib import admin
from django.contrib.admin.options import ActionLocation
from django.test import RequestFactory
from django.utils.translation import gettext as _

from admin.mixins import ActionLocationModelAdmin
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _request():
    request = RequestFactory().get("/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    return request


def _model_admin() -> ActionLocationModelAdmin:
    model_admin = admin.site._registry[BlogPost]
    assert isinstance(model_admin, ActionLocationModelAdmin)
    return model_admin


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


def test_dj_stripes_admins_list_their_actions_without_the_warning():
    """dj-stripe overrides ``get_actions`` with Django 5's signature;
    ``admin.mixins.StripeMirrorModelAdmin`` takes ``action_location``."""
    request = _request()
    stripe_admins = [
        model_admin
        for model, model_admin in admin.site._registry.items()
        if model._meta.app_label == "djstripe"
    ]
    listed = set()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for model_admin in stripe_admins:
            listed |= set(model_admin.get_actions(request))
            model_admin.get_action_choices(request)
            assert model_admin.get_action_choices(
                request, action_location=ActionLocation.CHANGE_FORM
            ) == [("", _("Select action"))]
    # dj-stripe's own changelist actions are still offered.
    assert {"_resync_instances", "_sync_all_instances", "_cancel"} <= listed
