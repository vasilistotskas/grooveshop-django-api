"""What the pipeline promises about EVERY registered email type.

Parametrised over ``registry.all_types()``, so a type added to any app's
``emails.py`` is covered the day it is registered, without editing a test:
a closed gate stops it with the gate's label, a store's switch stops a
switchable one and no switch exists for a required one, the headers are the
exact set its audience and kind call for, an opt-out stops marketing mail
and only that, and the preview is the mail that is sent.

Each case sends the type's own ``sample`` (an unsaved object graph) to an
explicit address, so no domain data is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail
from django.core.exceptions import ValidationError

from emails import optout
from emails.gates import CarrierGate, FeatureGate, ScheduleGate, StoreSwitchGate
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut, EmailSwitch
from emails.recipients import Recipient
from emails.registry import registry
from emails.render import render
from emails.send import Status, send_email
from emails.services import set_switch
from emails.types import Audience, EmailType, Kind, RenderEnv
from tests.utils.staff import store_staff

pytestmark = pytest.mark.django_db

RECIPIENT = Recipient("customer@example.test", "en")
TOKEN_STAMP = "1AbCdE"

ALL = registry.all_types()
GATED = [t for t in ALL if t.gates]
SWITCHABLE = [t for t in ALL if t.switchable]
REQUIRED = [t for t in ALL if t.required]
MARKETING = [t for t in ALL if t.kind is Kind.MARKETING]
TRANSACTIONAL = [t for t in ALL if t.kind is Kind.TRANSACTIONAL]


def ids(email_type: EmailType) -> str:
    return email_type.key


def sample_of(email_type: EmailType):
    return email_type.sample(RenderEnv(language="en", recipient=RECIPIENT))


@pytest.fixture(autouse=True)
def _every_gate_open(tenant, settings):
    """A bound store, the clock the signed tokens read fixed, and every gate
    open: each case then closes the one it is about."""
    settings.SECRET_KEY = "every-type-secret-key"
    mail.outbox.clear()
    with (
        patch.object(FeatureGate, "is_open", return_value=True),
        patch.object(StoreSwitchGate, "is_open", return_value=True),
        patch.object(CarrierGate, "is_open", return_value=True),
        patch.object(ScheduleGate, "is_open", return_value=True),
        patch(
            "django.core.signing.TimestampSigner.timestamp",
            return_value=TOKEN_STAMP,
        ),
    ):
        yield


def send(email_type: EmailType):
    return send_email(
        email_type.key, sample_of(email_type), recipient=RECIPIENT
    )


@pytest.mark.parametrize("email_type", ALL, ids=ids)
def test_an_open_type_sends_its_sample(email_type):
    [delivery] = send(email_type)

    assert delivery.status is Status.SENT
    [message] = mail.outbox
    assert message.to == [RECIPIENT.address]
    assert message.subject == delivery.subject


@pytest.mark.parametrize("email_type", GATED, ids=ids)
def test_a_closed_gate_sends_nothing_and_says_why(email_type):
    gate = email_type.gates[0]

    with patch.object(type(gate), "is_open", return_value=False):
        [delivery] = send(email_type)

    assert delivery.status is Status.SKIPPED_FEATURE_OFF
    assert delivery.reason == str(gate.label)
    assert mail.outbox == []


@pytest.mark.parametrize("email_type", SWITCHABLE, ids=ids)
def test_a_store_can_switch_a_switchable_type_off(email_type, tenant):
    set_switch(
        store_staff(tenant, is_superuser=True), email_type.key, enabled=False
    )

    [delivery] = send(email_type)

    assert delivery.status is Status.SKIPPED_SWITCHED_OFF
    assert mail.outbox == []


@pytest.mark.parametrize("email_type", REQUIRED, ids=ids)
def test_a_required_type_has_no_switch(email_type, tenant):
    staff = store_staff(tenant, is_superuser=True)

    with pytest.raises(ValidationError):
        set_switch(staff, email_type.key, enabled=False)

    assert not EmailSwitch.objects.exists()
    [delivery] = send(email_type)
    assert delivery.status is Status.SENT


@pytest.mark.parametrize("email_type", ALL, ids=ids)
def test_the_list_headers_are_exactly_what_the_audience_and_kind_call_for(
    email_type,
):
    send(email_type)

    [message] = mail.outbox
    headers = message.extra_headers
    if email_type.audience is not Audience.CUSTOMER:
        assert headers == {}
    elif email_type.kind is Kind.MARKETING:
        assert set(headers) == {
            "List-ID",
            "List-Unsubscribe",
            "List-Unsubscribe-Post",
        }
        assert headers["List-Unsubscribe-Post"] == (
            "List-Unsubscribe=One-Click"
        )
        assert "<https://" in headers["List-Unsubscribe"]
        assert "/api/v1/emails/unsubscribe/" in headers["List-Unsubscribe"]
    else:
        assert "List-Unsubscribe-Post" not in headers
        assert set(headers) <= {"List-ID", "List-Unsubscribe"}
        assert "List-ID" in headers


@pytest.mark.parametrize("email_type", MARKETING, ids=ids)
def test_an_opt_out_stops_marketing_mail(email_type):
    optout.opt_out(
        RECIPIENT.address,
        email_type.key,
        source=EmailOptOut.Source.ONE_CLICK,
    )

    [delivery] = send(email_type)

    assert delivery.status is Status.SKIPPED_OPTED_OUT
    assert mail.outbox == []


@pytest.mark.parametrize("email_type", MARKETING, ids=ids)
def test_opting_out_of_every_marketing_type_stops_each(email_type):
    optout.opt_out(
        RECIPIENT.address,
        EVERY_MARKETING_TYPE,
        source=EmailOptOut.Source.LINK,
    )

    [delivery] = send(email_type)

    assert delivery.status is Status.SKIPPED_OPTED_OUT


@pytest.mark.parametrize("email_type", TRANSACTIONAL, ids=ids)
def test_an_opt_out_never_stops_transactional_mail(email_type):
    optout.opt_out(
        RECIPIENT.address,
        EVERY_MARKETING_TYPE,
        source=EmailOptOut.Source.LINK,
    )

    [delivery] = send(email_type)

    assert delivery.status is Status.SENT


@pytest.mark.parametrize("email_type", ALL, ids=ids)
def test_the_preview_is_the_mail_that_is_sent(email_type):
    obj = sample_of(email_type)
    env = RenderEnv(language="en", recipient=RECIPIENT, is_preview=True)

    previewed = render(email_type, obj, env)
    send_email(email_type.key, obj, recipient=RECIPIENT)

    [message] = mail.outbox
    [(html, _mimetype)] = message.alternatives
    assert message.subject == previewed.subject
    assert message.body == previewed.text
    assert html == previewed.html
