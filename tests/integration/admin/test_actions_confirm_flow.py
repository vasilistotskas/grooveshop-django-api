"""Through the real URLs: a GET of a side-effect action shows its
confirmation and changes nothing; the confirmed POST does the work."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from b2b.factories import BusinessProfileFactory
from giftcard.enum import GiftCardStatus
from giftcard.factories import GiftCardFactory
from shipping_acs.factories import AcsShipmentFactory
from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

CONFIRMED = {"_form_submitted": "on"}


@pytest.fixture
def client():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True), backend=PLATFORM_STAFF_BACKEND_PATH
    )
    return client


def test_a_card_is_disabled_only_by_the_confirmed_post(client):
    card = GiftCardFactory()
    url = reverse("admin:giftcard_giftcard_disable_card", args=[card.pk])

    response = client.get(url)

    assert response.status_code == 200
    card.refresh_from_db()
    assert card.status != GiftCardStatus.DISABLED

    response = client.post(url, CONFIRMED)

    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse(
        "admin:giftcard_giftcard_change", args=[card.pk]
    )
    card.refresh_from_db()
    assert card.status == GiftCardStatus.DISABLED


def test_a_business_profile_is_suspended_only_by_the_confirmed_post(client):
    profile = BusinessProfileFactory()
    url = reverse("admin:b2b_businessprofile_suspend", args=[profile.pk])

    with patch("b2b.admin.B2BService.suspend") as suspend:
        assert client.get(url).status_code == 200
        suspend.assert_not_called()

        assert client.post(url, CONFIRMED).status_code == 200
        suspend.assert_called_once()


def test_a_tracking_poll_is_queued_only_by_the_confirmed_post(client):
    shipment = AcsShipmentFactory()
    url = reverse(
        "admin:shipping_acs_acsshipment_repoll_tracking", args=[shipment.pk]
    )

    with patch("shipping_acs.tasks.poll_acs_tracking_one.delay") as poll:
        assert client.get(url).status_code == 200
        poll.assert_not_called()

        assert client.post(url, CONFIRMED).status_code == 200
        poll.assert_called_once_with(shipment.pk)
