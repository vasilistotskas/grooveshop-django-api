"""What happens to a guest newsletter row after the form: it follows its
address to an account once that account proves it owns the address,
it is part of that person's GDPR export and erasure, and it is purged
if never confirmed."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed, user_signed_up
from django.utils import timezone

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.services.gdpr import anonymise_and_delete_user, compile_user_data
from user.tasks import purge_unconfirmed_guest_subscriptions
from user.utils.subscription import claim_guest_subscriptions

pytestmark = pytest.mark.django_db

Status = UserSubscription.SubscriptionStatus


@pytest.fixture
def topic():
    return SubscriptionTopic.objects.create(
        slug="newsletter",
        name="Newsletter",
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        is_default=True,
    )


def _guest(topic, email="guest@example.com", **kwargs):
    return UserSubscription.objects.create(
        topic=topic,
        email=email,
        source=UserSubscription.Source.NEWSLETTER_FORM,
        consent_text="I agree.",
        consent_ip="203.0.113.9",
        **kwargs,
    )


def _address(user, email, *, verified):
    EmailAddress.objects.filter(user=user).delete()
    return EmailAddress.objects.create(
        user=user, email=email, verified=verified, primary=True
    )


class TestClaim:
    def test_email_confirmed_claims_guest_rows(self, topic):
        guest = _guest(topic, status=Status.ACTIVE)
        user = UserAccountFactory(email="guest@example.com")
        address = _address(user, "Guest@Example.com", verified=True)

        email_confirmed.send(
            sender=EmailAddress, request=None, email_address=address
        )

        guest.refresh_from_db()
        assert guest.user == user
        # The consent record travels with the row.
        assert guest.consent_text == "I agree."

    def test_unverified_address_claims_nothing(self, topic):
        guest = _guest(topic)
        user = UserAccountFactory(email="guest@example.com")
        address = _address(user, "guest@example.com", verified=False)

        email_confirmed.send(
            sender=EmailAddress, request=None, email_address=address
        )
        user_signed_up.send(sender=type(user), request=None, user=user)

        guest.refresh_from_db()
        assert guest.user is None

    def test_signup_with_a_verified_address_claims(self, topic):
        """A social signup arrives with the address already verified, and
        no email_confirmed follows."""
        guest = _guest(topic)
        user = UserAccountFactory(email="guest@example.com")
        _address(user, "guest@example.com", verified=True)

        user_signed_up.send(
            sender=type(user),
            request=None,
            user=user,
            sociallogin=MagicMock(),
        )

        guest.refresh_from_db()
        assert guest.user == user

    def test_conflict_active_guest_row_wins(self, topic):
        guest = _guest(topic, status=Status.ACTIVE)
        user = UserAccountFactory(email="guest@example.com")
        own = UserSubscription.objects.create(
            user=user, topic=topic, status=Status.UNSUBSCRIBED
        )

        assert claim_guest_subscriptions(user, "guest@example.com") == 1

        assert not UserSubscription.objects.filter(pk=own.pk).exists()
        guest.refresh_from_db()
        assert guest.user == user

    def test_conflict_active_account_row_wins(self, topic):
        guest = _guest(topic, status=Status.PENDING)
        user = UserAccountFactory(email="guest@example.com")
        own = UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )

        assert claim_guest_subscriptions(user, "guest@example.com") == 0

        assert not UserSubscription.objects.filter(pk=guest.pk).exists()
        own.refresh_from_db()
        assert own.status == Status.ACTIVE

    def test_conflict_neither_active_keeps_the_account_row(self, topic):
        guest = _guest(topic, status=Status.PENDING)
        user = UserAccountFactory(email="guest@example.com")
        own = UserSubscription.objects.create(
            user=user, topic=topic, status=Status.UNSUBSCRIBED
        )

        claim_guest_subscriptions(user, "guest@example.com")

        assert not UserSubscription.objects.filter(pk=guest.pk).exists()
        assert UserSubscription.objects.filter(pk=own.pk).exists()


class TestGdpr:
    def test_export_includes_guest_rows_of_verified_addresses(self, topic):
        user = UserAccountFactory(email="owner@example.com")
        _address(user, "owner@example.com", verified=True)
        guest = _guest(topic, email="OWNER@example.com")
        stranger = _guest(topic, email="stranger@example.com")

        exported = compile_user_data(user)["subscriptions"]

        ids = {row["id"] for row in exported}
        assert guest.id in ids
        assert stranger.id not in ids
        entry = next(row for row in exported if row["id"] == guest.id)
        assert entry["consent_text"] == "I agree."
        assert entry["consent_ip"] == "203.0.113.9"

    def test_export_skips_guest_rows_of_unverified_addresses(self, topic):
        """An unverified address may be someone else's — their consent
        record (IP, user agent) is not this account's to download."""
        user = UserAccountFactory(email="owner@example.com")
        _address(user, "owner@example.com", verified=False)
        guest = _guest(topic, email="owner@example.com")

        ids = {row["id"] for row in compile_user_data(user)["subscriptions"]}
        assert guest.id not in ids

    def test_erasure_deletes_guest_rows_of_verified_addresses(self, topic):
        user = UserAccountFactory(email="owner@example.com")
        _address(user, "owner@example.com", verified=True)
        guest = _guest(topic, email="owner@example.com")
        stranger = _guest(topic, email="stranger@example.com")

        counts = anonymise_and_delete_user(user)

        assert counts["guest_subscriptions"] == 1
        assert not UserSubscription.objects.filter(pk=guest.pk).exists()
        assert UserSubscription.objects.filter(pk=stranger.pk).exists()


class TestPurge:
    def test_purges_only_stale_unconfirmed_guest_rows(self, topic):
        old = timezone.now() - timedelta(days=31)
        stale = _guest(topic, email="stale@example.com", status=Status.PENDING)
        fresh = _guest(topic, email="fresh@example.com", status=Status.PENDING)
        confirmed = _guest(
            topic, email="confirmed@example.com", status=Status.ACTIVE
        )
        account_row = UserSubscription.objects.create(
            user=UserAccountFactory(), topic=topic, status=Status.PENDING
        )
        UserSubscription.objects.filter(
            pk__in=[stale.pk, confirmed.pk, account_row.pk]
        ).update(confirmation_sent_at=old)
        UserSubscription.objects.filter(pk=fresh.pk).update(
            confirmation_sent_at=timezone.now() - timedelta(days=29)
        )

        result = purge_unconfirmed_guest_subscriptions.apply().result

        assert result == {"status": "success", "deleted": 1}
        assert not UserSubscription.objects.filter(pk=stale.pk).exists()
        assert (
            UserSubscription.objects.filter(
                pk__in=[fresh.pk, confirmed.pk, account_row.pk]
            ).count()
            == 3
        )


class TestConfirmationRetry:
    def test_a_failed_send_is_retried_three_times_five_minutes_apart(
        self, topic
    ):
        from unittest.mock import patch

        from user.tasks import send_subscription_confirmation_email_task

        row = UserSubscription(topic=topic, email="retry@example.com")
        row.arm_confirmation()
        row.save()

        assert send_subscription_confirmation_email_task.max_retries == 3
        assert (
            send_subscription_confirmation_email_task.default_retry_delay == 300
        )
        with (
            patch(
                "user.utils.subscription.send_subscription_confirmation",
                side_effect=OSError("smtp down"),
            ),
            patch.object(
                send_subscription_confirmation_email_task,
                "retry",
                side_effect=RuntimeError("retry scheduled"),
            ) as retry,
        ):
            with pytest.raises(RuntimeError, match="retry scheduled"):
                send_subscription_confirmation_email_task.run(row.id)
        retry.assert_called_once()
