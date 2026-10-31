"""The account emails: their types, builders and samples.

The data-export link, the newsletter's double opt-in and the re-engagement
mail. ``emails.EmailsConfig`` imports this module at boot; the tasks
(``user.tasks``, ``core.tasks``) choose WHEN to send and keep the
bookkeeping after a send.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django_stubs_ext import StrOrPromise

from core.utils.i18n import get_user_language
from core.utils.tenant_urls import (
    get_tenant_base_url,
    get_tenant_frontend_url,
    localize_storefront_url,
)
from emails.declare import customer_email, marketing_email
from emails.gates import FeatureGate, ScheduleGate
from emails.recipients import Recipient, customer_of
from emails.types import RenderEnv
from user.utils.subscription import (
    generate_blanket_unsubscribe_link,
    subscription_confirmation_url,
)

if TYPE_CHECKING:
    from user.models import UserAccount, UserDataExport, UserSubscription

TEMPLATE_DIR = "emails/user"

#: The beat entry that sends the re-engagement mail. It is disabled on
#: production, which is what the gate reports as "not scheduled".
REENGAGEMENT_BEAT_ENTRY = "send-inactive-user-notifications"

SAMPLE_USER_ID = 12345


def _data_export(export: UserDataExport, env: RenderEnv) -> dict[str, Any]:
    return {
        "user": export.user,
        "download_url": get_tenant_frontend_url(
            f"/account/settings/privacy?export={export.token}",
            language=env.language,
        ),
        "expires_at": export.expires_at,
        "file_size_kb": round(export.file_size / 1024, 1),
    }


def _confirmation(
    subscription: UserSubscription, env: RenderEnv
) -> dict[str, Any]:
    # parler fixes a model's language when it is loaded: state it, so the
    # topic's name and description are the recipient's.
    topic = subscription.topic
    topic.set_current_language(env.language)
    return {
        "user": subscription.user,
        "topic": topic,
        "topic_name": topic.safe_translation_getter("name", any_language=True),
        "subscription": subscription,
        "confirmation_url": subscription_confirmation_url(
            subscription.confirmation_token, language=env.language
        ),
    }


def _reengagement(user: UserAccount, env: RenderEnv) -> dict[str, Any]:
    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "email": user.email,
        },
        "unsubscribe_url": generate_blanket_unsubscribe_link(user),
    }


def _subscriber(subscription: UserSubscription) -> list[Recipient]:
    """The subscription's address, in the language it was made in; a guest
    row has no account, so the store's language when it recorded none."""
    return [
        Recipient(
            address=subscription.recipient_email,
            language=subscription.language
            or get_user_language(subscription.user),
        )
    ]


def _sample_user(env: RenderEnv) -> UserAccount:
    from user.models import UserAccount

    return UserAccount(
        id=SAMPLE_USER_ID,
        email=env.recipient.address,
        username="maria",
        first_name="Maria",
        last_name="Papadopoulou",
        language_code=env.language,
    )


def _sample_data_export(env: RenderEnv) -> UserDataExport:
    from user.models import UserDataExport

    return UserDataExport(
        user=_sample_user(env),
        token="sample-token",
        file_size=2048,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _sample_subscription(env: RenderEnv) -> UserSubscription:
    from user.models import SubscriptionTopic, UserSubscription

    topic = SubscriptionTopic(
        slug="weekly-news", category=SubscriptionTopic.TopicCategory.MARKETING
    )
    for code, _label in settings.LANGUAGES:
        topic.set_current_language(code)
        topic.name = "Weekly news"
        topic.description = "Our weekly digest."
    topic.set_current_language(env.language)
    return UserSubscription(
        user=_sample_user(env),
        topic=topic,
        language=env.language,
        confirmation_token="sample-token",
        subscribed_at=timezone.now(),
    )


customer_email(
    "user.data_export_ready",
    title=_("Data export ready"),
    description=_(
        "Sent when the archive of a customer's personal data is ready to "
        "download."
    ),
    area="account",
    templates=f"{TEMPLATE_DIR}/data_export_ready",
    subject=gettext_noop("Your data export is ready"),
    build_context=_data_export,
    sample=_sample_data_export,
    recipients=lambda export: [customer_of(export.user)],
)
customer_email(
    "newsletter.confirmation",
    title=_("Newsletter confirmation"),
    description=_(
        "Sent to confirm a subscription before it starts (double opt-in)."
    ),
    area="account",
    templates="emails/subscription/confirmation",
    subject=gettext_noop("Confirm your subscription to {topic_name}"),
    build_context=_confirmation,
    sample=_sample_subscription,
    recipients=_subscriber,
    gates=(FeatureGate(feature="newsletter", label=_("Newsletter feature")),),
)
marketing_email(
    "user.reengagement",
    title=_("We miss you"),
    description=_(
        "Sent to a customer who has not signed in for a long while, a few "
        "times at most."
    ),
    area="account",
    templates=f"{TEMPLATE_DIR}/inactive_user_email_template",
    subject=gettext_noop("We miss you!"),
    build_context=_reengagement,
    sample=_sample_user,
    recipients=lambda user: [customer_of(user)],
    list_id="reengagement",
    gates=(
        ScheduleGate(
            entry=REENGAGEMENT_BEAT_ENTRY,
            label=_("Scheduled re-engagement run"),
        ),
    ),
)


# --- allauth ---------------------------------------------------------
#
# allauth builds each of its mails itself and hands the adapter a template
# prefix and a context (``UserAccountAdapter.send_mail``). The prefix names
# the type; the context travels as text, because the mail is sent from a
# task (a transient SMTP failure is then a retry, never a failed sign-up).

#: The context keys allauth fills that the templates read. Everything else
#: it passes (the request, the account row, the reset key) is dropped.
ALLAUTH_CONTEXT_KEYS = frozenset(
    {
        "activate_url",
        "code",
        "deleted_email",
        "from_email",
        "ip",
        "password_reset_url",
        "provider",
        "signup_url",
        "timestamp",
        "to_email",
        "user_agent",
        "username",
    }
)

#: The links allauth resolves from ``HEADLESS_FRONTEND_URLS`` before it
#: knows the mail's language; the builder puts that language on them.
ALLAUTH_STOREFRONT_URL_KEYS = (
    "activate_url",
    "password_reset_url",
    "signup_url",
)

#: allauth's template prefix to the key of the type that sends it. The
#: coverage test fails for a prefix allauth sends that is not here.
ALLAUTH_TYPES: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class AllauthMail:
    """One allauth mail: who it goes to, in which language, and the
    context its templates read, as text."""

    address: str
    language: str
    user: UserAccount | None = None
    context: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_allauth(
        cls, *, address: str, language: str, context: Mapping[str, Any]
    ) -> AllauthMail:
        return cls(
            address=address,
            language=language,
            user=context.get("user"),
            context={
                key: value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                for key, value in context.items()
                if key in ALLAUTH_CONTEXT_KEYS and value is not None
            },
        )

    def task_kwargs(self, type_key: str) -> dict[str, Any]:
        """What ``send_allauth_email_task`` is queued with."""
        return {
            "type_key": type_key,
            "address": self.address,
            "language": self.language,
            "user_pk": self.user.pk if self.user is not None else None,
            "context": dict(self.context),
        }


def _allauth_context(mail: AllauthMail, env: RenderEnv) -> dict[str, Any]:
    context: dict[str, Any] = {
        **mail.context,
        "email": mail.address,
        "user": mail.user,
    }
    if "timestamp" in context:
        context["timestamp"] = datetime.fromisoformat(context["timestamp"])
    for key in ALLAUTH_STOREFRONT_URL_KEYS:
        if key in context:
            context[key] = localize_storefront_url(
                context[key], language=env.language
            )
    return context


def _allauth_sample(
    **context: str,
) -> Callable[[RenderEnv], AllauthMail]:
    def sample(env: RenderEnv) -> AllauthMail:
        return AllauthMail(
            address=env.recipient.address,
            language=env.language,
            user=_sample_user(env),
            context={
                "ip": "203.0.113.7",
                "user_agent": "Mozilla/5.0 (sample browser)",
                "timestamp": timezone.now().isoformat(),
                **context,
            },
        )

    return sample


def _sample_url(path: str) -> str:
    return f"{get_tenant_base_url()}{path}"


def _allauth_email(
    prefix: str,
    key: str,
    *,
    title: StrOrPromise,
    description: StrOrPromise,
    subject: str,
    sample: Callable[[RenderEnv], AllauthMail],
    templates: str = "",
) -> None:
    """Declare the type allauth's *prefix* is sent as. Account security
    and sign-in mail is required: a store cannot switch it off."""
    ALLAUTH_TYPES[prefix] = key
    customer_email(
        key,
        title=title,
        description=description,
        area="account",
        templates=templates or f"emails/{key.replace('.', '/')}",
        subject=subject,
        build_context=_allauth_context,
        sample=sample,
        recipients=lambda mail: [Recipient(mail.address, mail.language)],
    )


_allauth_email(
    "account/email/email_confirmation_signup",
    "account.email_confirmation_signup",
    title=_("Email confirmation at sign-up"),
    description=_(
        "Sent after sign-up to confirm the address, with a link or a code."
    ),
    templates="emails/account/email_confirmation",
    subject=gettext_noop("Please confirm your email address"),
    sample=_allauth_sample(
        activate_url=_sample_url("/account/verify-email/sample-key")
    ),
)
_allauth_email(
    "account/email/email_confirmation",
    "account.email_confirmation",
    title=_("Email confirmation"),
    description=_(
        "Sent to confirm an address a customer added to an existing account."
    ),
    subject=gettext_noop("Please confirm your email address"),
    sample=_allauth_sample(code="123456"),
)
_allauth_email(
    "account/email/password_reset_key",
    "account.password_reset_key",
    title=_("Password reset link"),
    description=_("Sent when a customer asks to reset their password."),
    subject=gettext_noop("Reset your password"),
    sample=_allauth_sample(
        password_reset_url=_sample_url("/account/password/reset/key/sample"),
        username="maria",
    ),
)
_allauth_email(
    "account/email/password_reset_code",
    "account.password_reset_code",
    title=_("Password reset code"),
    description=_("Sent when a customer resets their password with a code."),
    subject=gettext_noop("Your password reset code"),
    sample=_allauth_sample(code="123456"),
)
_allauth_email(
    "account/email/password_reset",
    "account.password_reset",
    title=_("Password was reset"),
    description=_("Sent to tell a customer their password was just reset."),
    subject=gettext_noop("Your password was reset"),
    sample=_allauth_sample(),
)
_allauth_email(
    "account/email/password_changed",
    "account.password_changed",
    title=_("Password changed"),
    description=_("Sent to tell a customer their password was changed."),
    subject=gettext_noop("Your password was changed"),
    sample=_allauth_sample(),
)
_allauth_email(
    "account/email/password_set",
    "account.password_set",
    title=_("Password set"),
    description=_(
        "Sent to tell a customer a password was set on their account."
    ),
    subject=gettext_noop("A password was set on your account"),
    sample=_allauth_sample(),
)
_allauth_email(
    "account/email/email_changed",
    "account.email_changed",
    title=_("Email address changed"),
    description=_(
        "Sent to the old address when a customer changes their email."
    ),
    subject=gettext_noop("Your email address was changed"),
    sample=_allauth_sample(
        from_email="maria@example.test", to_email="maria@example.org"
    ),
)
_allauth_email(
    "account/email/email_deleted",
    "account.email_deleted",
    title=_("Email address removed"),
    description=_(
        "Sent to tell a customer an address was removed from their account."
    ),
    subject=gettext_noop("An email address was removed from your account"),
    sample=_allauth_sample(deleted_email="maria@example.org"),
)
_allauth_email(
    "account/email/login_code",
    "account.login_code",
    title=_("Sign-in code"),
    description=_("Sent when a customer signs in with a code."),
    subject=gettext_noop("Your sign-in code"),
    sample=_allauth_sample(code="123456"),
)
_allauth_email(
    "account/email/unknown_account",
    "account.unknown_account",
    title=_("No account for this address"),
    description=_(
        "Sent when someone tries to sign in with an address that has no "
        "account."
    ),
    subject=gettext_noop("No account found for this address"),
    sample=_allauth_sample(signup_url=_sample_url("/account/signup")),
)
_allauth_email(
    "account/email/account_already_exists",
    "account.account_already_exists",
    title=_("Account already exists"),
    description=_(
        "Sent when someone signs up with an address that already has an "
        "account."
    ),
    subject=gettext_noop("You already have an account"),
    sample=_allauth_sample(
        password_reset_url=_sample_url("/account/password/reset")
    ),
)
_allauth_email(
    "mfa/email/recovery_codes_generated",
    "mfa.recovery_codes_generated",
    title=_("New recovery codes"),
    description=_(
        "Sent when a customer generates new two-factor recovery codes."
    ),
    subject=gettext_noop("New recovery codes were generated"),
    sample=_allauth_sample(),
)
_allauth_email(
    "mfa/email/totp_activated",
    "mfa.totp_activated",
    title=_("Authenticator app activated"),
    description=_("Sent when a customer turns on an authenticator app."),
    subject=gettext_noop("Authenticator app activated"),
    sample=_allauth_sample(),
)
_allauth_email(
    "mfa/email/totp_deactivated",
    "mfa.totp_deactivated",
    title=_("Authenticator app deactivated"),
    description=_("Sent when a customer turns off their authenticator app."),
    subject=gettext_noop("Authenticator app deactivated"),
    sample=_allauth_sample(),
)
_allauth_email(
    "mfa/email/webauthn_added",
    "mfa.webauthn_added",
    title=_("Security key added"),
    description=_("Sent when a customer adds a security key or passkey."),
    subject=gettext_noop("A security key was added"),
    sample=_allauth_sample(),
)
_allauth_email(
    "mfa/email/webauthn_removed",
    "mfa.webauthn_removed",
    title=_("Security key removed"),
    description=_("Sent when a customer removes a security key or passkey."),
    subject=gettext_noop("A security key was removed"),
    sample=_allauth_sample(),
)
_allauth_email(
    "socialaccount/email/account_connected",
    "social.account_connected",
    title=_("Social account connected"),
    description=_(
        "Sent when a customer connects a third-party sign-in to their account."
    ),
    subject=gettext_noop("A third-party account was connected"),
    sample=_allauth_sample(provider="Google"),
)
_allauth_email(
    "socialaccount/email/account_disconnected",
    "social.account_disconnected",
    title=_("Social account disconnected"),
    description=_(
        "Sent when a customer disconnects a third-party sign-in from their "
        "account."
    ),
    subject=gettext_noop("A third-party account was disconnected"),
    sample=_allauth_sample(provider="Google"),
)
