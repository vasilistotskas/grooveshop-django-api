"""A guest's product alert starts only once its address confirms it.

``POST /api/v1/product/alert`` is open to anonymous visitors, and the mail
an alert sends is marketing. A guest's alert is therefore stored inactive
and a confirmation email goes to the address; the click (a POST from the
storefront page the link opens) activates it and lifts the address's
opt-out of that alert's type, and only that one. The request answers the
same 202 for every address, so it tells nobody what the store knows.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail, signing
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from product.alerts import (
    CONFIRMATION_RESEND_COOLDOWN,
    TOKEN_SALT,
    make_confirmation_token,
)
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import (
    purge_unconfirmed_guest_alerts,
    send_product_alert_restock,
)
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory
from user.models.subscription import CONFIRMATION_TTL

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

GUEST = "guest@example.com"
RESTOCK = "product.restock_alert"
PRICE_DROP = "product.price_drop_alert"


@pytest.fixture
def product():
    return ProductFactory(
        active=True,
        stock=0,
        num_images=0,
        num_reviews=0,
        price_drop_alerts_enabled=True,
    )


def _post(product, email=GUEST, kind=ProductAlertKind.RESTOCK, client=None):
    body = {"kind": kind, "product": product.pk, "email": email}
    if kind == ProductAlertKind.PRICE_DROP:
        body |= {
            "targetPrice": str(product.price.amount - 1),
            "targetPriceCurrency": "EUR",
        }
    return (client or APIClient()).post(
        reverse("product-alert-list"),
        body,
        format="json",
        headers={"x-language": "en"},
    )


def _request(product, captured, **kwargs):
    with captured(execute=True):
        return _post(product, **kwargs)


def _confirm(token, method="post"):
    url = reverse("product-alert-confirm-by-token", args=[token])
    return getattr(APIClient(), method)(url)


def _pending(product, email=GUEST, kind=ProductAlertKind.RESTOCK, **fields):
    return ProductAlert.objects.create(
        kind=kind,
        product=product,
        email=email,
        is_active=False,
        confirmation_sent_at=timezone.now(),
        **fields,
    )


def _opted_out(address: str) -> set[str]:
    return set(
        EmailOptOut.objects.filter(
            address_hash=optout.address_hash(address)
        ).values_list("type_key", flat=True)
    )


class TestTheGuestRequest:
    def test_a_new_address_gets_a_pending_alert_and_one_confirmation(
        self, product, django_capture_on_commit_callbacks
    ):
        response = _request(product, django_capture_on_commit_callbacks)

        assert response.status_code == status.HTTP_202_ACCEPTED
        alert = ProductAlert.objects.get()
        assert alert.awaiting_confirmation
        assert not alert.is_active
        assert alert.language_code == "en"
        [message] = mail.outbox
        assert message.to == [GUEST]
        assert "/alerts/confirm/" in message.body

    def test_every_address_gets_the_same_answer(
        self, product, django_capture_on_commit_callbacks
    ):
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="live@example.com",
        )
        _pending(product, email="pending@example.com")
        optout.opt_out(
            "opted-out@example.com",
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ONE_CLICK,
        )

        answers = {
            email: _request(
                product, django_capture_on_commit_callbacks, email=email
            )
            for email in (
                "new@example.com",
                "live@example.com",
                "pending@example.com",
                "opted-out@example.com",
            )
        }

        assert {r.status_code for r in answers.values()} == {
            status.HTTP_202_ACCEPTED
        }
        assert len({str(r.json()) for r in answers.values()}) == 1

    def test_the_request_does_the_same_work_for_every_address(self, product):
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="live@example.com",
        )
        _pending(product, email="pending@example.com")
        optout.opt_out(
            "opted-out@example.com",
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ONE_CLICK,
        )

        observed = []
        for email in (
            "new@example.com",
            "live@example.com",
            "pending@example.com",
            "opted-out@example.com",
        ):
            with (
                patch(
                    "product.tasks.request_guest_product_alert_task.apply_async"
                ) as enqueue,
                CaptureQueriesContext(connection) as queries,
            ):
                response = _post(product, email=email)
            writes = [
                q["sql"]
                for q in queries.captured_queries
                if q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
            ]
            assert writes == [], email
            enqueue.assert_called_once()
            assert enqueue.call_args.kwargs["kwargs"]["email"] == email
            observed.append((response.json(), len(queries.captured_queries)))

        assert len({str(body) for body, _ in observed}) == 1
        assert len({count for _, count in observed}) == 1

    def test_a_pending_alert_is_not_asked_again_inside_the_cooldown(
        self, product, django_capture_on_commit_callbacks
    ):
        _pending(product)

        _request(product, django_capture_on_commit_callbacks)

        assert mail.outbox == []

    def test_a_pending_alert_is_asked_again_after_the_cooldown(
        self, product, django_capture_on_commit_callbacks
    ):
        _pending(product)
        ProductAlert.objects.update(
            confirmation_sent_at=timezone.now()
            - CONFIRMATION_RESEND_COOLDOWN
            - timedelta(seconds=1)
        )

        _request(product, django_capture_on_commit_callbacks)

        assert len(mail.outbox) == 1
        assert ProductAlert.objects.count() == 1

    def test_a_live_alert_is_left_alone(
        self, product, django_capture_on_commit_callbacks
    ):
        live = ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK, product=product, email=GUEST
        )

        _request(product, django_capture_on_commit_callbacks)

        live.refresh_from_db()
        assert live.is_live
        assert mail.outbox == []

    def test_an_opted_out_address_is_asked_to_confirm_again(
        self, product, django_capture_on_commit_callbacks
    ):
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK, product=product, email=GUEST
        )
        optout.opt_out(GUEST, RESTOCK, source=EmailOptOut.Source.LINK)

        _request(product, django_capture_on_commit_callbacks)

        assert ProductAlert.objects.get().awaiting_confirmation
        [message] = mail.outbox
        assert message.to == [GUEST]


class TestUnconfirmedAlertsSendNothing:
    def test_a_restock_skips_an_unconfirmed_alert(
        self, product, django_capture_on_commit_callbacks
    ):
        _request(product, django_capture_on_commit_callbacks)
        mail.outbox.clear()

        send_product_alert_restock.run(product.pk)

        assert mail.outbox == []
        assert ProductAlert.objects.get().awaiting_confirmation


class TestConfirm:
    def test_confirming_activates_the_alert(self, product):
        alert = _pending(product)

        response = _confirm(make_confirmation_token(alert))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "status": "confirmed",
            "kind": ProductAlertKind.RESTOCK,
        }
        alert.refresh_from_db()
        assert alert.is_live
        assert alert.confirmed_at is not None

    def test_a_confirmed_alert_sends_its_mail(self, product):
        alert = _pending(product)
        _confirm(make_confirmation_token(alert))
        mail.outbox.clear()

        send_product_alert_restock.run(product.pk)

        [message] = mail.outbox
        assert message.to == [GUEST]

    def test_confirming_lifts_only_that_types_blanket_opt_out(self, product):
        optout.opt_out(
            GUEST, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.ONE_CLICK
        )
        alert = _pending(product)

        _confirm(make_confirmation_token(alert))

        declined = _opted_out(GUEST)
        assert RESTOCK not in declined
        assert EVERY_MARKETING_TYPE not in declined
        assert PRICE_DROP in declined

    def test_confirming_lifts_only_that_types_own_opt_out(self, product):
        optout.opt_out(GUEST, RESTOCK, source=EmailOptOut.Source.LINK)
        optout.opt_out(GUEST, PRICE_DROP, source=EmailOptOut.Source.LINK)
        alert = _pending(product, kind=ProductAlertKind.PRICE_DROP)

        _confirm(make_confirmation_token(alert))

        assert _opted_out(GUEST) == {RESTOCK}

    def test_confirming_clears_the_accounts_flag_once_nothing_is_declined(
        self, product
    ):
        member = UserAccountFactory(
            num_addresses=0,
            email=GUEST,
            marketing_opted_out_at=timezone.now(),
        )
        optout.opt_out(GUEST, RESTOCK, source=EmailOptOut.Source.LINK)

        _confirm(make_confirmation_token(_pending(product)))

        member.refresh_from_db()
        assert member.marketing_opted_out_at is None

    def test_a_second_click_confirms_again_and_changes_nothing(self, product):
        alert = _pending(product)
        token = make_confirmation_token(alert)
        _confirm(token)
        alert.refresh_from_db()
        confirmed_at = alert.confirmed_at

        response = _confirm(token)

        assert response.status_code == status.HTTP_200_OK
        alert.refresh_from_db()
        assert alert.confirmed_at == confirmed_at

    def test_a_get_changes_nothing(self, product):
        alert = _pending(product)

        response = _confirm(make_confirmation_token(alert), method="get")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        alert.refresh_from_db()
        assert alert.awaiting_confirmation

    def test_a_tampered_token_is_refused(self, product):
        alert = _pending(product)
        token = make_confirmation_token(alert)

        response = _confirm(token[:-2] + ("aa" if token[-2:] != "aa" else "bb"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "confirmation_link_invalid"
        alert.refresh_from_db()
        assert alert.awaiting_confirmation

    def test_an_expired_token_is_refused(self, product):
        alert = _pending(product)
        # Minted a week and a minute ago.
        with patch(
            "django.core.signing.time.time",
            return_value=(
                timezone.now() - CONFIRMATION_TTL - timedelta(minutes=1)
            ).timestamp(),
        ):
            token = make_confirmation_token(alert)

        response = _confirm(token)

        assert response.status_code == status.HTTP_410_GONE
        assert response.json()["code"] == "confirmation_expired"
        alert.refresh_from_db()
        assert alert.awaiting_confirmation

    def test_another_stores_token_is_refused(self, product):
        alert = _pending(product)
        token = signing.dumps(
            {"schema": "another_store", "aid": str(alert.uuid)},
            salt=TOKEN_SALT,
        )

        response = _confirm(token)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        alert.refresh_from_db()
        assert alert.awaiting_confirmation

    def test_a_token_for_a_members_alert_is_refused(self, product):
        alert = ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            user=UserAccountFactory(num_addresses=0),
            is_active=False,
        )

        response = _confirm(make_confirmation_token(alert))

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_fired_alert_cannot_be_confirmed(self, product):
        alert = _pending(product)
        ProductAlert.objects.filter(pk=alert.pk).update(
            notified_at=timezone.now(), confirmation_sent_at=None
        )

        response = _confirm(make_confirmation_token(alert))

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestSweep:
    def test_expired_pending_alerts_are_deleted(self, product):
        stale = _pending(product, email="stale@example.com")
        ProductAlert.objects.filter(pk=stale.pk).update(
            confirmation_sent_at=timezone.now()
            - CONFIRMATION_TTL
            - timedelta(minutes=1)
        )
        fresh = _pending(product, email="fresh@example.com")
        confirmed = _pending(product, email="confirmed@example.com")
        _confirm(make_confirmation_token(confirmed))
        ProductAlert.objects.filter(pk=confirmed.pk).update(
            confirmation_sent_at=timezone.now() - CONFIRMATION_TTL * 2
        )
        member = ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            user=UserAccountFactory(num_addresses=0),
        )

        assert purge_unconfirmed_guest_alerts.run()["deleted"] == 1

        assert set(ProductAlert.objects.values_list("pk", flat=True)) == {
            fresh.pk,
            confirmed.pk,
            member.pk,
        }


class TestSignedInCustomers:
    def test_a_members_alert_is_active_at_once(self, product):
        client = APIClient()
        client.force_authenticate(UserAccountFactory(num_addresses=0))

        response = _post(product, client=client)

        assert response.status_code == status.HTTP_201_CREATED
        alert = ProductAlert.objects.get()
        assert alert.is_live
        assert alert.confirmation_sent_at is None
        assert mail.outbox == []
