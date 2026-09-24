"""The storefront API answers in the language the caller states.

``core.middleware.locale.RequestLanguageMiddleware`` activates
``resolve_request_language(request)`` — ``X-Language``, then
``X-Locale``, then ``Accept-Language``, clamped to the served languages —
for unprefixed ``/api/`` and ``/_allauth/`` requests. Before it,
``LocaleMiddleware`` forced ``LANGUAGE_CODE`` on every unprefixed path
(``prefix_default_language=False``), so an English page got Greek
validation errors, Greek ``detail`` messages and Greek parler fields:
``POST /api/subscriptions/newsletter?locale=en`` with ``consent: false``
answered ``Απαιτείται η συγκατάθεσή σας για την εγγραφή.`` in production.

Expected Greek text is computed with ``translation.override("el")``
rather than written out: CI has no ``compilemessages`` step, so this
repo's own catalogs may be absent there (DRF's and allauth's ship
compiled). What these tests pin is the language the request was
answered in, whichever catalog is installed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation
from django.utils.cache import get_cache_key
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from extra_settings.models import Setting
from rest_framework.test import APIClient

from core.middleware.locale import RequestLanguageMiddleware
from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.factories.variant_group import ProductVariantGroupFactory
from promotion.enum import PromotionTrigger
from promotion.factories.promotion import PromotionFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

NEWSLETTER = "/api/v1/user/subscription/newsletter"
LOGIN = "/_allauth/app/v1/auth/login"


def gettext_in(language, message):
    with translation.override(language):
        return str(translation.gettext(message))


@pytest.fixture
def client():
    return APIClient()


def _newsletter(client, language, **body):
    return client.post(
        NEWSLETTER,
        {"email": "probe@example.com", "consentText": "x", **body},
        format="json",
        headers={"x-language": language},
    )


class TestErrorMessages:
    def test_the_newsletter_consent_error_is_in_the_stated_language(
        self, client
    ):
        # The production probe, both ways round.
        en = _newsletter(client, "en", consent=False).json()
        el = _newsletter(client, "el", consent=False).json()

        assert en["consent"] == ["Consent is required to subscribe."]
        assert el["consent"] == [
            gettext_in("el", "Consent is required to subscribe.")
        ]

    def test_a_drf_validation_error_is_in_the_stated_language(self, client):
        en = _newsletter(client, "en", consent=True, email="").json()
        el = _newsletter(client, "el", consent=True, email="").json()

        assert en["email"] == ["This field may not be blank."]
        # DRF ships a compiled Greek catalog, so this is real Greek in CI.
        assert el["email"] == [gettext_in("el", "This field may not be blank.")]
        assert el["email"] != en["email"]

    def test_an_allauth_headless_error_is_in_the_stated_language(self, client):
        UserAccountFactory(email="shopper@example.com", password="right-pw")

        def message(language):
            response = client.post(
                LOGIN,
                {"email": "shopper@example.com", "password": "wrong-pw"},
                format="json",
                headers={"x-language": language},
            )
            assert response.status_code == 400
            return response.json()["errors"][0]["message"]

        en, el = message("en"), message("el")

        # allauth ships a compiled Greek catalog too.
        assert en != el
        assert en == gettext_in(
            "en",
            "The email address and/or password you specified are not correct.",
        )
        assert el == gettext_in(
            "el",
            "The email address and/or password you specified are not correct.",
        )

    def test_an_unsupported_language_falls_back_to_the_default(self, client):
        response = _newsletter(client, "fr", consent=False)

        assert response["Content-Language"] == "el"
        assert response.json()["consent"] == [
            gettext_in("el", "Consent is required to subscribe.")
        ]

    def test_x_locale_and_accept_language_are_the_fallbacks(self, client):
        via_locale = client.post(
            NEWSLETTER, {}, format="json", headers={"x-locale": "en"}
        )
        via_accept = client.post(
            NEWSLETTER, {}, format="json", headers={"accept-language": "en-US"}
        )

        assert via_locale["Content-Language"] == "en"
        assert via_accept["Content-Language"] == "en"


class TestTranslatedContent:
    def _variant_pair(self):
        attribute = AttributeFactory(active=True, is_variant=True)
        for language_code, name in (("el", "Χρώμα"), ("en", "Colour")):
            attribute.set_current_language(language_code)
            attribute.name = name
        attribute.save()
        value = AttributeValueFactory(attribute=attribute, active=True)
        for language_code, text in (("el", "Μαύρο"), ("en", "Black")):
            value.set_current_language(language_code)
            value.value = text
        value.save()

        group = ProductVariantGroupFactory(active=True)
        product = ProductFactory(active=True, variant_group=group)
        ProductAttributeFactory(product=product, attribute_value=value)
        return product

    def test_a_parler_field_is_read_in_the_stated_language(self, client):
        product = self._variant_pair()
        url = reverse("product-variants", args=[product.pk])

        en = client.get(url, headers={"x-language": "en"}).json()
        el = client.get(url, headers={"x-language": "el"}).json()

        assert en["axes"][0]["name"] == "Colour"
        assert en["axes"][0]["values"][0]["value"] == "Black"
        assert el["axes"][0]["name"] == "Χρώμα"
        assert el["axes"][0]["values"][0]["value"] == "Μαύρο"

    def test_an_explicit_language_code_still_wins(self, client):
        """``?languageCode=`` selects the content it names, whatever
        language the request is answered in."""
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, is_active=True
        )
        for language_code, name in (
            ("el", "Δωρεάν αποστολή"),
            ("en", "Free shipping"),
        ):
            row = promotion.translations.get(language_code=language_code)
            row.name = name
            row.save()

        original = Setting.get.__func__

        def _get(cls, key, default=None):
            if key == "PROMOTIONS_ENABLED":
                return True
            return original(cls, key, default)

        url = reverse("promotion:promotion-public-list")
        with patch.object(Setting, "get", classmethod(_get)):
            explicit = client.get(
                url, {"languageCode": "el"}, headers={"x-language": "en"}
            ).json()
            implicit = client.get(url, headers={"x-language": "en"}).json()

        assert explicit[0]["name"] == "Δωρεάν αποστολή"
        assert implicit[0]["name"] == "Free shipping"


class TestScopeAndHeaders:
    def test_api_responses_vary_on_the_language_headers(self, client):
        response = _newsletter(client, "en", consent=False)

        vary = {h.strip().lower() for h in response["Vary"].split(",")}
        assert {"x-language", "x-locale", "accept-language"} <= vary
        assert response["Content-Language"] == "en"

    def test_the_language_is_deactivated_after_the_response(self, client):
        with translation.override("el"):
            _newsletter(client, "en", consent=False)
            # Deactivated, not left on English for whatever runs next.
            assert translation.get_language() == "el"

    def test_request_language_code_is_set_for_the_view(self):
        request = RequestFactory().get(
            "/api/v1/product", headers={"x-language": "en"}
        )
        middleware = RequestLanguageMiddleware(lambda r: HttpResponse())

        middleware.process_request(request)
        # URL resolution still runs under the default language, which the
        # unprefixed routes are registered under.
        assert translation.get_language() == "el"

        middleware.process_view(request, None, (), {})
        assert request.LANGUAGE_CODE == "en"
        assert translation.get_language() == "en"
        translation.deactivate()

    def test_an_explicit_path_prefix_keeps_winning(self, client):
        with translation.override("en"):
            url = reverse("product-variants", args=[self._pk()])
        assert url.startswith("/en/")

        response = client.get(url, headers={"x-language": "el"})

        assert response["Content-Language"] == "en"

    def _pk(self):
        return ProductFactory(active=True).pk

    def test_the_admin_ignores_x_language(self, client):
        """The admin keeps its own rule: Greek unless the operator
        chose otherwise (``AdminDefaultGreekMiddleware``)."""
        response = client.get("/admin/login/", headers={"x-language": "en"})

        assert response["Content-Language"] == "el"
        vary = response.get("Vary", "").lower()
        assert "x-language" not in vary

    def test_the_prefixed_admin_is_unchanged(self, client):
        response = client.get("/en/admin/login/")

        assert response["Content-Language"] == "en"


class TestCacheKeying:
    """An en response is never served to an el request.

    ``cache_page`` suffixes its key with ``request.LANGUAGE_CODE``
    (``django/utils/cache.py`` ``_i18n_cache_key_suffix``), which the
    middleware sets. ``cache_methods`` is a no-op under pytest, so the
    decorators are composed directly, as in
    ``tests/unit/core/test_cache_methods_vary.py``.
    """

    def test_language_splits_the_cache_page_key(self):
        caches["default"].clear()
        rendered = []

        class View:
            def list(self, request):
                body = translation.get_language()
                rendered.append(body)
                return HttpResponse(body)

        View.list = method_decorator(cache_page(60, key_prefix="lang-probe"))(
            View.list
        )
        view = View()
        middleware = RequestLanguageMiddleware(view.list)

        def ask(language):
            # The handler's order: process_request, URL resolution,
            # process_view, the view (with cache_page inside it),
            # process_response.
            request = RequestFactory().get(
                "/api/v1/probe", headers={"x-language": language}
            )
            middleware.process_request(request)
            middleware.process_view(request, view.list, (), {})
            response = middleware.process_response(request, view.list(request))
            return request, response

        en_request, en_response = ask("en")
        el_request, el_response = ask("el")

        assert en_response.content == b"en"
        assert el_response.content == b"el"
        # The el request rendered its own body: it did not hit the en entry.
        assert rendered == ["en", "el"]
        assert get_cache_key(en_request, key_prefix="lang-probe") != (
            get_cache_key(el_request, key_prefix="lang-probe")
        )

        # And each language is served its own entry on a repeat.
        _, en_again = ask("en")
        assert en_again.content == b"en"
        assert rendered == ["en", "el"]
