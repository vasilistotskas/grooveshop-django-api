"""Integration tests for Knox token revocation signal handlers.

Covers ``revoke_knox_tokens_on_password_change`` (and the shared
``_revoke_knox_tokens`` / ``_broadcast_force_logout`` helpers) wired
to ``allauth.account.signals.password_changed``.

Signal handler location: ``user/signals.py``.

Design notes
------------
* The signal is fired by emitting ``password_changed`` manually via
  ``password_changed.send()``.  This avoids a full allauth view round-trip
  (which would need email verification, CSRF, etc.) while still exercising
  the real signal receiver and Knox ORM operations.
* ``_broadcast_force_logout`` calls ``channels.layers.get_channel_layer``
  which returns the Redis-backed layer in production.  In the test
  environment that layer is not running, so we patch ``get_channel_layer``
  to return ``None`` — the guard ``if layer:`` in the signal handler means
  that path is safely skipped without raising.
* Knox token creation uses ``get_token_model().objects.create(user=user)``
  as documented by django-rest-knox.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from allauth.account.signals import password_changed
from django.contrib.auth import get_user_model
from knox.models import get_token_model

from user.factories.account import UserAccountFactory

User = get_user_model()
AuthToken = get_token_model()

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_tokens(user, count: int = 1) -> list:
    """Create *count* Knox tokens for *user* and return the token instances."""
    return [AuthToken.objects.create(user)[0] for _ in range(count)]


def _emit_password_changed(user, request=None) -> None:
    """Fire the allauth ``password_changed`` signal for *user*.

    The signal handler ignores ``request`` content for revocation (it only
    uses ``user``), but the signal signature requires it.
    """
    from django.test import RequestFactory

    if request is None:
        request = RequestFactory().post("/fake/")
        request.user = user

    # Patch the channel layer so _broadcast_force_logout is a no-op.
    # ``get_channel_layer`` is imported lazily inside the helper, so the
    # patch must target ``channels.layers.get_channel_layer`` (its source
    # module) rather than ``user.signals.get_channel_layer`` — the latter
    # is not a module attribute because the import is function-local.
    with patch("channels.layers.get_channel_layer", return_value=None):
        password_changed.send(
            sender=User,
            request=request,
            user=user,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRevokeKnoxTokensOnPasswordChange:
    def test_password_change_revokes_all_knox_tokens_for_user(self):
        """Given a user with 3 Knox tokens, firing password_changed deletes all 3."""
        user = UserAccountFactory()
        _create_tokens(user, 3)

        assert AuthToken.objects.filter(user=user).count() == 3

        _emit_password_changed(user)

        assert AuthToken.objects.filter(user=user).count() == 0

    def test_password_change_only_affects_changing_user(self):
        """User A's tokens are deleted; User B's tokens remain intact."""
        user_a = UserAccountFactory()
        user_b = UserAccountFactory()

        _create_tokens(user_a, 2)
        _create_tokens(user_b, 2)

        assert AuthToken.objects.filter(user=user_a).count() == 2
        assert AuthToken.objects.filter(user=user_b).count() == 2

        _emit_password_changed(user_a)

        assert AuthToken.objects.filter(user=user_a).count() == 0
        # User B is untouched.
        assert AuthToken.objects.filter(user=user_b).count() == 2

    def test_password_change_with_no_existing_tokens_does_not_error(self):
        """Signal handler must not raise when the user has no Knox tokens."""
        user = UserAccountFactory()
        assert AuthToken.objects.filter(user=user).count() == 0

        # Should complete without exception.
        _emit_password_changed(user)

        assert AuthToken.objects.filter(user=user).count() == 0

    def test_password_change_revokes_tokens_for_correct_user_when_multiple_users_have_tokens(
        self,
    ):
        """Broader isolation check: three users, only the one whose password
        changed loses their tokens."""
        user_a = UserAccountFactory()
        user_b = UserAccountFactory()
        user_c = UserAccountFactory()

        _create_tokens(user_a, 1)
        _create_tokens(user_b, 3)
        _create_tokens(user_c, 2)

        _emit_password_changed(user_b)

        assert AuthToken.objects.filter(user=user_a).count() == 1
        assert AuthToken.objects.filter(user=user_b).count() == 0
        assert AuthToken.objects.filter(user=user_c).count() == 2

    def test_force_logout_broadcast_is_attempted_after_token_revocation(self):
        """_broadcast_force_logout is called even when the channel layer is
        available; here we verify it is invoked with the correct user group."""
        user = UserAccountFactory()
        _create_tokens(user, 1)

        group_sends: list[tuple] = []

        def fake_group_send(group, message):
            group_sends.append((group, message))

        fake_layer = type(
            "_FakeLayer",
            (),
            {"group_send": staticmethod(fake_group_send)},
        )()

        with (
            patch("channels.layers.get_channel_layer", return_value=fake_layer),
            patch(
                "asgiref.sync.async_to_sync",
                side_effect=lambda fn: fn,
            ),
        ):
            password_changed.send(
                sender=User,
                request=None,
                user=user,
            )

        # Tokens revoked.
        assert AuthToken.objects.filter(user=user).count() == 0
        # WS broadcast called with the user's tenant-scoped personal group.
        # Tests run in the public schema (DATABASE_ROUTERS disabled in
        # conftest), so the group prefix is "tenant_public".
        from notification.groups import user_group

        expected_group = user_group("public", user.pk)
        assert any(group == expected_group for group, _ in group_sends), (
            f"Expected group_send to target {expected_group}, got {group_sends}"
        )


class TestRevocationSignalHandlerRegistration:
    def test_signal_handler_is_connected(self):
        """The receiver is registered with the expected dispatch_uid so it
        fires exactly once per signal emission (no duplicate registrations)."""
        # Django's ``Signal.receivers`` items are 4-tuples in 5.x:
        #   ((dispatch_uid_or_receiver_id, sender_id), weakref, ..., ...)
        # The dispatch_uid (when supplied as a string at @receiver registration
        # time) sits at ``lookup_key[0]``; receiver_id (when no dispatch_uid)
        # is also at ``lookup_key[0]`` but as an int.  Filter to strings only
        # — those are the explicitly-named handlers.
        dispatch_uids = []
        for entry in password_changed.receivers:
            lookup_key = entry[0] if isinstance(entry, tuple) else entry
            if isinstance(lookup_key, tuple) and len(lookup_key) >= 1:
                first = lookup_key[0]
                if isinstance(first, str):
                    dispatch_uids.append(first)
        assert any(
            "user.revoke_knox_tokens_on_password_change" == s
            for s in dispatch_uids
        ), (
            "Expected dispatch_uid 'user.revoke_knox_tokens_on_password_change' "
            f"to be registered on password_changed. Found dispatch_uids: {dispatch_uids}"
        )
