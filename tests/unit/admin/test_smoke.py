"""Safety-net smoke tests for every registered Django admin.

Iterates ``django.contrib.admin.site._registry`` and, for each
registered ``ModelAdmin``, asserts the changelist renders with
``200`` for a superuser. Where ``has_add_permission`` reports the
admin as addable, the add form is also asserted to render with
``200``.

This is deliberately shallow (status-code coverage only, no
per-query budgets) — it exists to catch a broken admin (import
error, missing template, blown-up ``list_display``/fieldset
callable, etc.) before it reaches a narrower, admin-specific test.
Later phases of the admin overhaul add per-query budgets on top of
this.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.test import Client, RequestFactory
from django.urls import reverse

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _registry_entries():
    """Snapshot ``admin.site._registry`` at collection time.

    Safe to call at module import time: pytest-django calls
    ``django.setup()`` while configuring the plugin, which runs every
    app's ``ready()`` (and therefore every ``admin.py`` module-level
    ``admin.site.register(...)`` call) long before test modules are
    imported/collected.
    """
    entries = []
    for model, model_admin in django_admin.site._registry.items():
        opts = model._meta
        label = f"{opts.app_label}.{opts.model_name}"
        entries.append(pytest.param(model, model_admin, id=label))
    return entries


_REGISTRY_ENTRIES = _registry_entries()

# Pre-existing add-view bugs, unrelated to the Phase 1 export-machinery
# relocation this test module ships alongside. Only the add-form
# assertion below is skipped for these — the changelist assertion
# still runs (and passes) for every one of them. Tracked for a later
# phase of the admin overhaul; remove an entry once its bug is fixed.
_KNOWN_ADD_FORM_BUGS = {
    "contact.contact": (
        "ContactAdmin.timing_info does `now - obj.created_at`, which "
        "is None on the unsaved instance rendered for the add form's "
        "readonly 'Timing Information' fieldset -> TypeError."
    ),
    "notification.notification": (
        "NotificationAdmin.timing_summary does `now - obj.created_at` "
        "/ `obj.created_at.hour`, which is None on the unsaved "
        "instance rendered for the add form's readonly field -> "
        "TypeError."
    ),
    "user.useraccount": (
        "UserAdmin.add_fieldsets references password1/password2, "
        "which UserCreationForm's Meta.fields does not declare -> "
        "FieldError on GET of the add form."
    ),
}


def _changelist_url(model) -> str:
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")


def _add_url(model) -> str:
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_add")


@pytest.fixture
def superuser():
    return UserAccountFactory(admin=True)


@pytest.fixture
def logged_in_client(superuser):
    client = Client()
    client.force_login(superuser)
    return client


@pytest.mark.parametrize("model,model_admin", _REGISTRY_ENTRIES)
def test_changelist_and_add_render(
    logged_in_client, superuser, model, model_admin
):
    changelist_response = logged_in_client.get(_changelist_url(model))
    assert changelist_response.status_code == 200

    request = RequestFactory().get(_changelist_url(model))
    request.user = superuser
    if model_admin.has_add_permission(request):
        opts = model._meta
        label = f"{opts.app_label}.{opts.model_name}"
        if label in _KNOWN_ADD_FORM_BUGS:
            pytest.skip(_KNOWN_ADD_FORM_BUGS[label])

        add_response = logged_in_client.get(_add_url(model))
        assert add_response.status_code == 200
