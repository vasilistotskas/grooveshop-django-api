"""Mail for the store's own staff speaks the store's language.

``send_admin_new_order_email`` set its subject in English whatever the
store spoke, and its body in "the active language", which in a worker is
the platform's. The subject and body now render under the language of
the bound store (``Tenant.default_locale``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core import mail

from b2b.factories import BusinessProfileFactory
from b2b.tasks import send_admin_new_business_profile_email
from order.factories.order import OrderFactory
from order.tasks import send_admin_new_order_email
from tests.utils.emails import open_email_gates  # noqa: F401

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

CONTACT = "owner@store.test"


@pytest.fixture(autouse=True)
def _contact_email():
    with patch(
        "emails.recipients.tenant_operator_addresses", return_value=[CONTACT]
    ):
        yield


@pytest.fixture
def store(bind_tenant):
    def _store(locale: str) -> None:
        bind_tenant(SimpleNamespace(default_locale=locale))

    return _store


@pytest.fixture
def order():
    order = OrderFactory(num_order_items=1)
    mail.outbox.clear()
    return order


class TestNewOrderEmail:
    @pytest.mark.parametrize(
        ("locale", "subject"),
        [("en", "New order — #{id}"), ("el", "Νέα παραγγελία — #{id}")],
    )
    def test_subject_is_in_the_stores_language(
        self, store, order, locale, subject
    ):
        store(locale)

        assert send_admin_new_order_email.run(order.id)

        [sent] = mail.outbox
        assert sent.subject == subject.format(id=order.id)
        assert sent.to == [CONTACT]


class TestNewBusinessProfileEmail:
    @pytest.mark.parametrize(
        ("locale", "subject"),
        [
            ("en", "New B2B application — Acme"),
            ("el", "Νέα αίτηση B2B — Acme"),
        ],
    )
    def test_subject_is_in_the_stores_language(self, store, locale, subject):
        profile = BusinessProfileFactory(company_name="Acme")
        mail.outbox.clear()
        store(locale)

        send_admin_new_business_profile_email.run(profile.id)

        [sent] = mail.outbox
        assert sent.subject == subject
