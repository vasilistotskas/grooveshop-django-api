from __future__ import annotations

import logging

from allauth.account.signals import (
    authentication_step_completed,
    email_changed,
    password_changed,
    password_reset,
    user_signed_up,
)
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils.crypto import get_random_string

from django.db import transaction

from user.models.subscription import SubscriptionTopic, UserSubscription

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialLogin

User = get_user_model()


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth-event logging.
#
# django.request only logs "Unauthorized: /_allauth/..." for auth traffic,
# which cannot distinguish a wrong password from a pending-2FA step from a
# code request — that ambiguity made the 2026-07 login incidents hard to
# debug. These receivers give each auth event one structured INFO line.
# django.contrib.auth signals fire for every login path (password, code,
# social, 2FA completion) because allauth funnels through auth_login();
# allauth's authentication_step_completed adds the per-step granularity
# (e.g. password accepted but 2FA still pending). Never log credentials.
# ---------------------------------------------------------------------------


@receiver(user_logged_in, dispatch_uid="user.log_auth_login")
def log_auth_login(sender, request, user, **kwargs):
    logger.info(
        "Auth: login completed",
        extra={
            "user_id": user.pk,
            "path": getattr(request, "path", None),
        },
    )


@receiver(
    authentication_step_completed, dispatch_uid="user.log_auth_step_completed"
)
def log_auth_step_completed(sender, request, user, method, **kwargs):
    # Fires per completed step; NOT full sign-in (2FA may still be pending).
    logger.info(
        "Auth: step completed",
        extra={
            "user_id": getattr(user, "pk", None),
            "method": str(method),
            "path": getattr(request, "path", None),
        },
    )


@receiver(user_login_failed, dispatch_uid="user.log_auth_login_failed")
def log_auth_login_failed(sender, credentials, request=None, **kwargs):
    # `credentials` may contain the password — extract identifiers only.
    identifier = None
    if isinstance(credentials, dict):
        identifier = credentials.get("email") or credentials.get("username")
    logger.warning(
        "Auth: login failed",
        extra={
            "identifier": identifier,
            "path": getattr(request, "path", None),
        },
    )


@receiver(user_logged_out, dispatch_uid="user.log_auth_logout")
def log_auth_logout(sender, request, user, **kwargs):
    logger.info(
        "Auth: logout",
        extra={
            "user_id": getattr(user, "pk", None),
            "path": getattr(request, "path", None),
        },
    )


def _revoke_knox_tokens(user) -> int:
    """Delete all Knox tokens for *user* and return the count removed."""
    from knox.models import get_token_model  # noqa: PLC0415

    result = get_token_model().objects.filter(user=user).delete()
    return result[0]


def _broadcast_force_logout(user) -> None:
    """Push a force.logout event to the user's WebSocket group."""
    from asgiref.sync import async_to_sync  # noqa: PLC0415
    from channels.layers import get_channel_layer  # noqa: PLC0415

    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(
            f"user_{user.pk}", {"type": "force.logout"}
        )


@receiver(
    password_changed, dispatch_uid="user.revoke_knox_tokens_on_password_change"
)
def revoke_knox_tokens_on_password_change(request, user, **kwargs):
    """
    Revoke all Knox access tokens when the user changes their password.
    ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True handles allauth sessions;
    this ensures Knox tokens (used for REST API + WebSocket) are also
    invalidated so any active API clients lose access immediately.
    """
    revoked = _revoke_knox_tokens(user)
    logger.info(
        "Revoked Knox tokens after password change",
        extra={"user_id": user.pk, "revoked_count": revoked},
    )
    _broadcast_force_logout(user)


@receiver(
    password_reset, dispatch_uid="user.revoke_knox_tokens_on_password_reset"
)
def revoke_knox_tokens_on_password_reset(request, user, **kwargs):
    """
    Revoke all Knox access tokens after a forgot-password reset.
    password_changed covers in-session changes; this covers the
    email-link/code reset path where the user is not logged in and
    ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE does not apply.
    """
    revoked = _revoke_knox_tokens(user)
    logger.info(
        "Revoked Knox tokens after password reset",
        extra={"user_id": user.pk, "revoked_count": revoked},
    )
    _broadcast_force_logout(user)


@receiver(email_changed, dispatch_uid="user.revoke_knox_tokens_on_email_change")
def revoke_knox_tokens_on_email_change(
    request, user, from_email_address, to_email_address, **kwargs
):
    """
    Revoke all Knox access tokens when the user changes their primary email.

    A changed email is equivalent to a changed identity — existing tokens
    may have been issued on the basis of the old email and should be
    considered stale.  ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE handles allauth
    sessions; we mirror that behaviour for Knox tokens + WebSocket here.
    """
    revoked = _revoke_knox_tokens(user)
    logger.info(
        "Revoked Knox tokens after email change",
        extra={
            "user_id": user.pk,
            "revoked_count": revoked,
            "from_email": str(from_email_address),
            "to_email": str(to_email_address),
        },
    )
    _broadcast_force_logout(user)


@receiver(user_signed_up, dispatch_uid="user.populate_profile")
def populate_profile(
    sociallogin: SocialLogin | None = None, user=None, **kwargs
):
    """Download the provider avatar for new social account signups."""
    if not sociallogin or not user:
        # Email/password signups have no sociallogin — nothing to do.
        return

    provider = sociallogin.account.provider
    picture_url = None

    match provider:
        case "facebook":
            picture_url = f"http://graph.facebook.com/{sociallogin.account.uid}/picture?type=large"
        case "google":
            picture_url = sociallogin.account.extra_data.get("picture")
        case "discord":
            avatar_id = sociallogin.account.extra_data.get("id")
            avatar_hash = sociallogin.account.extra_data.get("avatar")
            if avatar_id and avatar_hash:
                picture_url = f"https://cdn.discordapp.com/avatars/{avatar_id}/{avatar_hash}.png"
            else:
                logger.warning(
                    "Missing avatar_id or avatar_hash for Discord provider"
                )
        case "github":
            picture_url = sociallogin.account.extra_data.get("avatar_url")
        case _:
            logger.warning("Unsupported social provider: %s", provider)

    if picture_url:
        # Dispatch after the outer transaction commits so the worker can
        # always read the new user row.  (Social signups run inside an
        # atomic block; firing bare .delay() risks the task reading before
        # the INSERT is visible to the Celery worker's DB connection.)
        from user.tasks import download_social_avatar_task  # noqa: PLC0415

        transaction.on_commit(
            lambda url=picture_url: download_social_avatar_task.delay(
                user_id=user.pk, picture_url=url
            )
        )


@receiver(
    post_save, sender=User, dispatch_uid="user.create_default_subscriptions"
)
def create_default_subscriptions(sender, instance, created, **kwargs):
    if created:
        default_topics = SubscriptionTopic.objects.filter(
            is_active=True, is_default=True
        )

        for topic in default_topics:
            subscription = UserSubscription.objects.create(
                user=instance,
                topic=topic,
                status=(
                    UserSubscription.SubscriptionStatus.PENDING
                    if topic.requires_confirmation
                    else UserSubscription.SubscriptionStatus.ACTIVE
                ),
            )

            if topic.requires_confirmation:
                subscription.confirmation_token = get_random_string(64)
                subscription.save()
                # Dispatch only after the outer transaction commits so the
                # worker can't read the row before it's persisted.
                from user.tasks import send_subscription_confirmation_email_task

                transaction.on_commit(
                    lambda s=subscription: (
                        send_subscription_confirmation_email_task.delay(s.id)
                    )
                )
