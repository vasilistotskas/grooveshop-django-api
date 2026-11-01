"""The platform's own mail: what the catalogue says about it."""

from __future__ import annotations

from emails.registry import registry
from emails.types import Audience, Kind, Owner

PLATFORM_KEYS = {
    "platform.billing_expiring",
    "platform.billing_expired",
    "platform.billing_suspended",
    "platform.billing_stage_copy",
    "platform.staff_password_reset",
    "platform.health_alert",
    "platform.backup_failed",
}


def test_the_platform_sends_exactly_these_types():
    assert {t.key for t in registry.types_for(Owner.PLATFORM)} == PLATFORM_KEYS


def test_a_platform_type_is_required_transactional_and_never_for_customers():
    for email_type in registry.types_for(Owner.PLATFORM):
        assert email_type.required, email_type.key
        assert email_type.kind is Kind.TRANSACTIONAL, email_type.key
        assert email_type.audience is not Audience.CUSTOMER, email_type.key


def test_every_operator_alert_goes_to_the_platform_operators():
    alerts = [
        t
        for t in registry.types_for(Owner.PLATFORM)
        if t.audience is Audience.PLATFORM_OPERATOR
    ]

    assert {t.key for t in alerts} == {
        "platform.billing_stage_copy",
        "platform.health_alert",
        "platform.backup_failed",
    }
