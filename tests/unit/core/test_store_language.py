"""The language a message uses when its reader stated none: the store's."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.conf import settings
from django.utils import translation

from core.utils.i18n import (
    active_language_code,
    get_order_language,
    get_user_language,
    store_language,
)


@pytest.fixture
def store(bind_tenant):
    return lambda locale: bind_tenant(SimpleNamespace(default_locale=locale))


class TestStoreLanguage:
    def test_is_the_bound_stores_default_locale(self, store):
        store("en")

        assert store_language() == "en"

    @pytest.mark.parametrize("locale", ["", None])
    def test_a_store_without_one_uses_the_platforms(self, store, locale):
        store(locale)

        assert store_language() == settings.LANGUAGE_CODE

    def test_no_store_uses_the_platforms(self, bind_tenant):
        bind_tenant(None)

        assert store_language() == settings.LANGUAGE_CODE


class TestWhoStatedNoLanguage:
    def test_a_guest_gets_the_stores(self, store):
        store("en")

        assert get_user_language(None) == "en"

    def test_an_account_that_recorded_none_gets_the_stores(self, store):
        store("en")

        assert get_user_language(SimpleNamespace(language_code="")) == "en"

    def test_an_account_keeps_its_own(self, store):
        store("en")

        assert get_user_language(SimpleNamespace(language_code="de")) == "de"

    def test_an_order_without_a_language_gets_the_stores(self, store):
        store("en")

        order = SimpleNamespace(language_code="", user=None)

        assert get_order_language(order) == "en"
        assert get_order_language(None) == "en"


class TestActiveLanguageCode:
    def test_is_the_request_language_when_served(self, store):
        store("el")

        with translation.override("de"):
            assert active_language_code() == "de"

    def test_a_regional_variant_is_clamped_to_its_language(self, store):
        store("el")

        with translation.override("en-gb"):
            assert active_language_code() == "en"

    def test_an_unserved_language_is_the_stores(self, store, monkeypatch):
        store("en")
        monkeypatch.setattr(translation, "get_language", lambda: "xx")
        monkeypatch.setattr("core.utils.i18n.get_language", lambda: "xx")

        assert active_language_code() == "en"
