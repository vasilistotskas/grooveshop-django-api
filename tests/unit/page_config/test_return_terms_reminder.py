"""Saving the return policy page says what Google is told about returns.

The storefront gives Google the return window and who pays from the
store settings (``RETURN_WINDOW_DAYS``, ``RETURN_FEES``), not from this
page's text, so the two can drift apart when only the page is edited. The
admin says which terms Google gets, every time the page is saved.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from extra_settings.models import Setting

from page_config.admin import ContentPageAdmin
from page_config.models import ContentPage

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _request():
    request = RequestFactory().post("/admin/")
    request.user = get_user_model()(is_superuser=True)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _save(slug, *, days, fees):
    Setting.objects.update_or_create(
        name="RETURN_WINDOW_DAYS",
        defaults={"value_type": "int", "value_int": days},
    )
    Setting.objects.update_or_create(
        name="RETURN_FEES",
        defaults={"value_type": "string", "value_string": fees},
    )
    page = ContentPage(slug=slug, is_published=True)
    request = _request()
    ContentPageAdmin(ContentPage, django_admin.site).save_model(
        request, page, form=None, change=False
    )
    return [str(message) for message in get_messages(request)]


def test_names_the_window_and_who_pays_when_the_return_policy_is_saved():
    (message,) = _save("return-policy", days=14, fees="customer")

    assert "14 days" in message
    assert "paid by the customer" in message
    assert "RETURN_WINDOW_DAYS" in message
    assert "RETURN_FEES" in message


def test_says_one_day_in_the_singular():
    (message,) = _save("return-policy", days=1, fees="free")

    assert "return window 1 day " in message
    assert "paid by the store" in message


def test_says_not_stated_for_terms_the_store_has_not_set():
    (message,) = _save("return-policy", days=0, fees="")

    assert message.count("not stated") == 2


def test_says_nothing_when_another_page_is_saved():
    assert _save("terms", days=14, fees="customer") == []
