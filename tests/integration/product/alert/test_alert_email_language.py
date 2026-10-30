"""A guest's alert email speaks the language they asked in.

A guest has no account language, so the restock and price-drop emails
fell back to the platform's language whatever the visitor or the store
spoke. The alert now remembers the language the visitor subscribed in
(``X-Language``), and without one the email uses the store's.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import send_product_alert_restock
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

EN_SUBJECT = "is back in stock"
EL_SUBJECT = "είναι ξανά διαθέσιμο"


@pytest.fixture
def product():
    return ProductFactory(active=True, num_images=0, num_reviews=0)


def _subscribe(product, **headers):
    return APIClient().post(
        reverse("product-alert-list"),
        {
            "kind": ProductAlertKind.RESTOCK,
            "product": product.pk,
            "email": "guest@example.com",
        },
        format="json",
        **headers,
    )


class TestAlertRemembersTheVisitorsLanguage:
    def test_the_language_header_is_stored(self, product):
        response = _subscribe(product, headers={"X-Language": "en"})

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert ProductAlert.objects.get().language_code == "en"

    def test_an_unserved_language_is_not_stored(self, product):
        _subscribe(product, headers={"X-Language": "xx"})

        assert ProductAlert.objects.get().language_code == ""

    def test_no_language_stated_is_not_stored(self, product):
        _subscribe(product)

        assert ProductAlert.objects.get().language_code == ""


class TestAlertEmailLanguage:
    def _send(self, product):
        mail.outbox.clear()
        send_product_alert_restock.run(product.pk)
        [sent] = mail.outbox
        return sent.subject

    def test_a_guest_gets_the_language_they_subscribed_in(self, product):
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="guest@example.com",
            language_code="en",
        )

        assert EN_SUBJECT in self._send(product)

    def test_a_guest_without_one_gets_the_stores(self, product, bind_tenant):
        bind_tenant(SimpleNamespace(default_locale="en"))
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="guest@example.com",
        )

        assert EN_SUBJECT in self._send(product)

    def test_a_member_keeps_their_own_language(self, product, bind_tenant):
        bind_tenant(SimpleNamespace(default_locale="en"))
        member = UserAccountFactory(num_addresses=0, language_code="el")
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            user=member,
            language_code="en",
        )

        assert EL_SUBJECT in self._send(product)
