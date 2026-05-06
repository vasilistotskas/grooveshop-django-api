"""Regression tests for the Meta CAPI event builders.

The single load-bearing assertion here is that ``Event.action_source``
is set to the ``ActionSource.WEBSITE`` enum value rather than the
raw string ``"website"``. ``facebook_business`` 25.0.1 tightened
type validation; the string form raises ``TypeError`` at dispatch
time but tests that mock ``MetaCapiClient.send`` (i.e. all unit
tests) miss it because the failure is in the SDK's runtime check,
not in the builder. This file exercises the builder directly so
the type check fires under pytest.
"""

from __future__ import annotations

import pytest
from facebook_business.adobjects.serverside.action_source import ActionSource

from meta_capi.services import _action_source_website, _new_event


class TestActionSource:
    def test_action_source_helper_returns_website_enum(self):
        result = _action_source_website()
        assert result is ActionSource.WEBSITE
        assert isinstance(result, ActionSource)

    def test_action_source_is_not_a_plain_string(self):
        # Catch a regression to ``ACTION_SOURCE_WEBSITE = "website"``.
        # ``Event.action_source`` setter raises TypeError on a string
        # under ``facebook_business`` 25.0.1+, which silently torpedoes
        # every CAPI dispatch in production (the audit row lands as
        # ``status=failed`` with no further visibility).
        result = _action_source_website()
        assert not isinstance(result, str)


class TestNewEventActionSource:
    def test_new_event_uses_website_action_source(self):
        from facebook_business.adobjects.serverside.user_data import UserData

        event = _new_event(
            name="CompleteRegistration",
            event_id="test-event-id",
            user_data=UserData(),
            custom_data=None,
            event_source_url="https://webside.gr/account/signup",
        )

        # Reading back the property value is what would also have
        # blocked us in production: the SDK's setter rejects a
        # raw string, so a regression to the string form would
        # never even reach this line.
        assert event.action_source is ActionSource.WEBSITE

    def test_new_event_normalize_does_not_raise(self):
        """``Event.normalize`` is what the dispatch task calls before
        sending and again before logging the audit payload. With a
        bad ``action_source`` it raises ``TypeError on value: website``
        — that's the exact error we saw in prod.
        """

        from facebook_business.adobjects.serverside.user_data import UserData

        event = _new_event(
            name="CompleteRegistration",
            event_id="test-event-id",
            user_data=UserData(),
            custom_data=None,
            event_source_url="https://webside.gr/",
        )

        try:
            event.normalize()
        except TypeError as exc:  # pragma: no cover — regression sentinel
            pytest.fail(
                f"Event.normalize() raised TypeError, indicating a"
                f" regression to a raw-string ``action_source``: {exc}"
            )
