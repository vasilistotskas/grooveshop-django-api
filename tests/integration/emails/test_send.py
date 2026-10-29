"""``send_email``: what reaches the outbox, and why something does not.

The registry holds no production type yet, so the contract is proven over
probe types (``tests/utils/emails.py``). Each case states the one rule it
checks, and the closed-gate, switch and opt-out cases would fail if the
check they exercise were deleted from the pipeline.
"""

from __future__ import annotations

import logging
from smtplib import SMTPServerDisconnected
from unittest import mock

import pytest
from django.core import mail

from emails import optout
from emails.gates import FeatureGate, StoreSwitchGate
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut, EmailSwitch
from emails.recipients import Recipient
from emails.registry import UnknownEmailType
from emails.render import render
from emails.send import Status, StoreRequired, send_email, switched_off_keys
from emails.types import Audience, Kind, Owner, RenderEnv
from tests.integration.emails.conftest import STORE_INBOX
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db

CUSTOMER = Recipient("customer@example.test", "en")


@pytest.fixture(autouse=True)
def _outbox(tenant):
    mail.outbox.clear()


def statuses(deliveries) -> list[Status]:
    return [delivery.status for delivery in deliveries]


class TestUnknownType:
    def test_it_raises_and_sends_nothing(self, catalogue):
        catalogue(probe())

        with pytest.raises(UnknownEmailType):
            send_email("probe.missing", probe_object())

        assert mail.outbox == []


class TestSending:
    def test_a_message_is_built_from_the_type_and_the_object(self, catalogue):
        catalogue(probe("order.shipped", list_id="shipping_notification"))

        deliveries = send_email("order.shipped", probe_object(marker="BODY"))

        assert statuses(deliveries) == [Status.SENT]
        [message] = mail.outbox
        assert message.subject == "Your Order #7 Has Shipped"
        assert message.to == ["customer@example.test"]
        assert message.from_email == "Acme Gadgets <noreply@platform.test>"
        assert message.reply_to == [STORE_INBOX]
        assert "BODY" in message.body
        [(html, mimetype)] = message.alternatives
        assert mimetype == "text/html"
        assert "<p>BODY</p>" in html
        assert message.extra_headers == {
            "List-ID": "<shipping_notification.shop.example>",
            "List-Unsubscribe": f"<mailto:{STORE_INBOX}?subject=unsubscribe>",
        }

    def test_an_explicit_recipient_replaces_the_types_resolver(self, catalogue):
        catalogue(probe())

        send_email(
            "probe.notice",
            probe_object(),
            recipient=Recipient("explicit@example.test", "en"),
        )

        [message] = mail.outbox
        assert message.to == ["explicit@example.test"]

    def test_every_recipient_gets_a_mail_in_their_own_language(self, catalogue):
        catalogue(
            probe(
                recipients=lambda obj: [
                    Recipient("greek@example.test", "el"),
                    Recipient("english@example.test", "en"),
                ]
            )
        )

        deliveries = send_email("probe.notice", probe_object())

        assert statuses(deliveries) == [Status.SENT, Status.SENT]
        assert {m.to[0]: m.subject for m in mail.outbox} == {
            "greek@example.test": "Η παραγγελία σας #7 έχει αποσταλεί",
            "english@example.test": "Your Order #7 Has Shipped",
        }

    def test_nobody_to_write_to_is_a_skip(self, catalogue):
        catalogue(probe(recipients=lambda obj: []))

        deliveries = send_email("probe.notice", probe_object())

        assert statuses(deliveries) == [Status.SKIPPED_NO_RECIPIENT]
        assert mail.outbox == []


class TestPreviewEqualsSend:
    def test_the_rendered_preview_is_the_message_that_is_sent(self, catalogue):
        [email_type] = catalogue(probe())
        env = RenderEnv(language="en", recipient=CUSTOMER, is_preview=True)
        sample = email_type.sample(env)

        previewed = render(email_type, sample, env)
        send_email("probe.notice", sample, recipient=CUSTOMER)

        [message] = mail.outbox
        assert message.subject == previewed.subject
        assert message.body == previewed.text
        assert message.alternatives == [(previewed.html, "text/html")]


class TestGates:
    def test_a_closed_gate_sends_nothing_and_says_why(self, catalogue):
        gate = FeatureGate(feature="gift_cards", label="Gift cards feature")
        catalogue(probe(gates=(gate,)))

        with mock.patch("emails.gates.feature_on", return_value=False):
            deliveries = send_email("probe.notice", probe_object())

        assert mail.outbox == []
        [delivery] = deliveries
        assert delivery.status is Status.SKIPPED_FEATURE_OFF
        assert delivery.reason == gate.label

    def test_an_open_gate_sends(self, catalogue):
        catalogue(
            probe(
                gates=(FeatureGate(feature="gift_cards", label="Gift cards"),)
            )
        )

        with mock.patch("emails.gates.feature_on", return_value=True):
            deliveries = send_email("probe.notice", probe_object())

        assert statuses(deliveries) == [Status.SENT]

    def test_every_gate_must_be_open_and_the_first_closed_one_is_named(
        self, catalogue
    ):
        open_gate = FeatureGate(feature="gift_cards", label="Gift cards")
        closed = StoreSwitchGate(setting="MYDATA_ENABLED", label="myDATA")
        also_closed = FeatureGate(feature="cart", label="Cart")
        catalogue(probe(gates=(open_gate, closed, also_closed)))

        with (
            mock.patch(
                "emails.gates.feature_on",
                side_effect=lambda name: name == "gift_cards",
            ),
            mock.patch("emails.gates.store_setting", return_value=False),
        ):
            [delivery] = send_email("probe.notice", probe_object())

        assert delivery.reason == "myDATA"
        assert mail.outbox == []

    def test_a_closed_gate_also_stops_a_required_type(self, catalogue):
        """Required means never switched off by the store; a feature that
        does not exist for the store still has no mail."""
        catalogue(
            probe(
                required=True,
                gates=(FeatureGate(feature="gift_cards", label="Gift cards"),),
            )
        )

        with mock.patch("emails.gates.feature_on", return_value=False):
            [delivery] = send_email("probe.notice", probe_object())

        assert delivery.status is Status.SKIPPED_FEATURE_OFF
        assert mail.outbox == []


class TestSwitch:
    def test_a_switched_off_type_sends_nothing(self, catalogue):
        catalogue(probe())
        EmailSwitch.objects.create(type_key="probe.notice")

        [delivery] = send_email("probe.notice", probe_object())

        assert delivery.status is Status.SKIPPED_SWITCHED_OFF
        assert mail.outbox == []

    def test_another_types_switch_does_not_stop_it(self, catalogue):
        catalogue(probe(), probe("probe.other"))
        EmailSwitch.objects.create(type_key="probe.other")

        deliveries = send_email("probe.notice", probe_object())

        assert statuses(deliveries) == [Status.SENT]

    def test_a_required_type_ignores_a_row_for_it(self, catalogue):
        """No code path writes one (``set_switch`` refuses), but a required
        mail must not depend on that."""
        catalogue(probe(required=True))
        EmailSwitch.objects.create(type_key="probe.notice")

        deliveries = send_email("probe.notice", probe_object())

        assert statuses(deliveries) == [Status.SENT]

    def test_a_gate_is_reported_before_the_switch(self, catalogue):
        catalogue(
            probe(gates=(FeatureGate(feature="cart", label="Cart feature"),))
        )
        EmailSwitch.objects.create(type_key="probe.notice")

        with mock.patch("emails.gates.feature_on", return_value=False):
            [delivery] = send_email("probe.notice", probe_object())

        assert delivery.status is Status.SKIPPED_FEATURE_OFF

    def test_the_switch_read_is_cached_until_a_switch_changes(
        self, catalogue, django_assert_num_queries
    ):
        catalogue(probe())
        send_email("probe.notice", probe_object())

        with django_assert_num_queries(0):
            switched_off_keys()


class TestOptOut:
    def test_an_opted_out_recipient_gets_no_marketing(self, catalogue):
        catalogue(probe("cart.abandoned", kind=Kind.MARKETING))
        optout.opt_out(
            CUSTOMER.address, "cart.abandoned", source=EmailOptOut.Source.LINK
        )

        [delivery] = send_email("cart.abandoned", probe_object())

        assert delivery.status is Status.SKIPPED_OPTED_OUT
        assert mail.outbox == []

    def test_the_star_row_stops_every_marketing_type(self, catalogue):
        catalogue(probe("cart.abandoned", kind=Kind.MARKETING))
        optout.opt_out(
            CUSTOMER.address,
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ACCOUNT,
        )

        [delivery] = send_email("cart.abandoned", probe_object())

        assert delivery.status is Status.SKIPPED_OPTED_OUT

    def test_only_the_opted_out_recipient_is_skipped(self, catalogue):
        catalogue(
            probe(
                "cart.abandoned",
                kind=Kind.MARKETING,
                recipients=lambda obj: [
                    CUSTOMER,
                    Recipient("other@example.test", "en"),
                ],
            )
        )
        optout.opt_out(
            CUSTOMER.address, "cart.abandoned", source=EmailOptOut.Source.LINK
        )

        deliveries = send_email("cart.abandoned", probe_object())

        assert statuses(deliveries) == [
            Status.SKIPPED_OPTED_OUT,
            Status.SENT,
        ]
        assert [m.to for m in mail.outbox] == [["other@example.test"]]

    def test_a_row_does_not_stop_a_transactional_type(self, catalogue):
        catalogue(probe("order.shipped"))
        EmailOptOut.objects.create(
            address_hash=optout.address_hash(CUSTOMER.address),
            type_key=EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.STAFF,
        )
        EmailOptOut.objects.create(
            address_hash=optout.address_hash(CUSTOMER.address),
            type_key="order.shipped",
            source=EmailOptOut.Source.STAFF,
        )

        deliveries = send_email("order.shipped", probe_object())

        assert statuses(deliveries) == [Status.SENT]


class TestHeaders:
    def test_marketing_carries_the_one_click_unsubscribe(self, catalogue):
        catalogue(probe("cart.abandoned", kind=Kind.MARKETING))

        send_email("cart.abandoned", probe_object())

        [message] = mail.outbox
        headers = message.extra_headers
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert "https://api.shop.example" in headers["List-Unsubscribe"]
        assert headers["List-ID"] == "<cart-abandoned.shop.example>"

    def test_transactional_never_claims_one_click(self, catalogue):
        catalogue(probe("order.shipped"))

        send_email("order.shipped", probe_object())

        [message] = mail.outbox
        assert "List-Unsubscribe-Post" not in message.extra_headers


class TestOwner:
    def test_a_store_type_needs_a_bound_store(self, catalogue, tenant):
        catalogue(probe())
        from tests.utils.staff import bind_store_tenant

        bind_store_tenant(None)

        with pytest.raises(StoreRequired):
            send_email("probe.notice", probe_object())

        assert mail.outbox == []

    def test_a_platform_type_is_sent_as_the_platform(self, catalogue, settings):
        settings.SITE_NAME = "GrooveShop Platform"
        catalogue(
            probe(owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR)
        )

        send_email("probe.notice", probe_object())

        [message] = mail.outbox
        assert (
            message.from_email == "GrooveShop Platform <noreply@platform.test>"
        )
        assert message.reply_to == []


class TestFailure:
    def test_a_transport_failure_is_logged_and_raised(self, catalogue, caplog):
        catalogue(probe())

        with (
            mock.patch(
                "emails.transport.EmailMultiAlternatives.send",
                side_effect=SMTPServerDisconnected("relay down"),
            ),
            caplog.at_level(logging.WARNING, logger="emails.send"),
            pytest.raises(SMTPServerDisconnected),
        ):
            send_email("probe.notice", probe_object())

        assert "probe.notice" in caplog.text
        assert CUSTOMER.address not in caplog.text


class TestHeaderInjection:
    def test_a_subject_parameter_cannot_add_a_header_or_a_recipient(
        self, catalogue
    ):
        catalogue(probe(subject="{marker}"))

        send_email(
            "probe.notice",
            probe_object(marker="Sale\r\nBcc: attacker@evil.test"),
        )

        [message] = mail.outbox
        assert message.subject == "Sale Bcc: attacker@evil.test"
        assert message.bcc == []
        assert message.to == ["customer@example.test"]
        assert "attacker" not in str(message.message()["Bcc"])

    def test_an_address_with_a_line_break_is_refused_by_django(self, catalogue):
        catalogue(probe())

        with pytest.raises(ValueError, match="Bcc|newline|line"):
            send_email(
                "probe.notice",
                probe_object(),
                recipient=Recipient(
                    "victim@example.test\r\nBcc: attacker@evil.test", "en"
                ),
            )

        assert mail.outbox == []
