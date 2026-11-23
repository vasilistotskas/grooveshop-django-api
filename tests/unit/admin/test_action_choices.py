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


def _every_admin():
    from admin.platform_site import platform_admin_site

    return [
        *admin.site._registry.values(),
        *platform_admin_site._registry.values(),
    ]


def test_every_admin_takes_action_location():
    """Django warns only through its own wrappers
    (``_get_actions_with_action_location``,
    ``_get_action_choices_with_action_location``), which read the
    signature: every admin of both sites must accept it."""
    from django.utils.inspect import get_func_args

    missing = [
        f"{type(model_admin).__name__}.{method}"
        for model_admin in _every_admin()
        for method in ("get_actions", "get_action_choices")
        if "action_location" not in get_func_args(getattr(model_admin, method))
    ]
    assert missing == []


def test_no_action_location_deprecation_warning():
    """Through the wrapper the changelist calls, as Django does."""
    request = _request()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _model_admin()._get_action_choices_with_action_location(request)


def _stripe_admin(model_name: str):
    from django.apps import apps

    return admin.site._registry[
        apps.get_app_config("djstripe").get_model(model_name)
    ]


def test_dj_stripes_admins_list_their_actions_without_the_warning():
    """dj-stripe overrides ``get_actions`` with Django 5's signature;
    ``admin.mixins.StripeMirrorModelAdmin`` takes ``action_location`` and
    keeps each admin's own choice of actions."""
    request = _request()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for model_admin in _every_admin():
            if model_admin.model._meta.app_label != "djstripe":
                continue
            model_admin._get_action_choices_with_action_location(request)
            assert (
                model_admin._get_actions_with_action_location(
                    request, action_location=ActionLocation.CHANGE_FORM
                )
                == {}
            )
        discount = set(_stripe_admin("Discount").get_actions(request))
        mandate = set(_stripe_admin("Mandate").get_actions(request))
        webhook = set(_stripe_admin("WebhookEndpoint").get_actions(request))
        subscription = set(_stripe_admin("Subscription").get_actions(request))
        customer = set(_stripe_admin("Customer").get_actions(request))

    assert not discount & {"_resync_instances", "_sync_all_instances"}
    assert "_sync_all_instances" not in mandate
    assert "delete_selected" not in webhook
    assert "_cancel" in subscription
    assert {"_resync_instances", "_sync_all_instances"} <= customer


def test_a_stripe_action_refused_by_its_form_runs_without_the_warning():
    """dj-stripe's confirm view runs the action again on an invalid form by
    indexing its tuple; the project's runs its ``func``."""
    from unittest.mock import MagicMock

    from django.http import QueryDict

    from admin.mixins import _confirm_stripe_custom_action

    view = _confirm_stripe_custom_action().view_class()
    view.request = _request()
    view.kwargs = {"model_name": "customer", "action_name": "_resync_instances"}
    form = MagicMock(data=QueryDict(""), errors={})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        view.form_invalid(form)
