from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from datetime import timedelta

from extra_settings.models import Setting

from core.utils.email_context import build_email_context
from core.utils.tenant_urls import (
    get_tenant_api_base_url,
    get_tenant_base_url,
)
from tenant.credentials import (
    tenant_contact_email,
    tenant_from_email,
)
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext as _

from core.utils.i18n import get_user_language

from user.models.subscription import SubscriptionTopic, UserSubscription

logger = logging.getLogger(__name__)

# Unsubscribe links are signed with ``django.core.signing`` under a
# dedicated salt (NOT the password-reset generator) so the token is
# purpose-scoped, tamper-proof, and — crucially — does NOT invalidate when
# the user logs in or changes their password. ``UNSUBSCRIBE_MAX_AGE`` gives
# the link a long life so a newsletter that sits in an inbox for months
# still honours one-click unsubscribe (RFC 8058), while still bounding the
# validity of a leaked token.
UNSUBSCRIBE_SALT = "user.unsubscribe"
UNSUBSCRIBE_MAX_AGE = timedelta(days=365)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from user.models.account import UserAccount as User
else:
    User = get_user_model()


def send_subscription_confirmation(
    subscription: UserSubscription, user: User
) -> bool:
    if check_subscription_before_send(
        user=user, topic_slug=subscription.topic.slug
    ):
        logger.warning(
            f"Attempted to send confirmation for already active subscription {subscription.id}"
        )
        return False

    if subscription.status != UserSubscription.SubscriptionStatus.PENDING:
        logger.warning(
            f"Attempted to send confirmation for non-pending subscription {subscription.id}"
        )
        return False

    if not subscription.confirmation_token:
        logger.error(
            f"No confirmation token for subscription {subscription.id}"
        )
        return False

    try:
        # SUBSCRIPTION_CONFIRMATION_URL is a relative path template
        # (e.g. "/api/v1/user/subscription/confirm/{token}").  We
        # prepend the current tenant's API base URL at send time so the
        # link is always correct for the tenant that owns the request,
        # rather than relying on API_BASE_URL baked into the setting at
        # startup.
        url_path_template = Setting.get("SUBSCRIPTION_CONFIRMATION_URL")
        # The confirmation URL points at a Django API endpoint
        # (``/api/v1/user/subscription/confirm/<token>``), NOT the
        # storefront. There is no Nuxt proxy for that path, so we must
        # build against the tenant's API origin — a platform-wide
        # ``API_BASE_URL`` would 404 for every non-platform tenant.
        api_base = get_tenant_api_base_url()
        # SUBSCRIPTION_CONFIRMATION_URL is a RELATIVE path template by
        # contract (see EXTRA_SETTINGS_DEFAULTS) — an absolute value
        # stored per-tenant would pin every tenant's confirmation links
        # to one host. Cutover normalizes any absolute rows
        # (MULTI_TENANT_CUTOVER.md §0.3).
        confirmation_url = f"{api_base}{url_path_template}".format(
            token=subscription.confirmation_token
        )

        user = subscription.user
        language = get_user_language(user)

        context = build_email_context(
            user=user,
            topic=subscription.topic,
            subscription=subscription,
            confirmation_url=confirmation_url,
            LANGUAGE_CODE=language,
        )

        with translation.override(language):
            subject = _("Confirm your subscription to {topic}").format(
                topic=subscription.topic.name
            )
            html_message = render_to_string(
                "emails/subscription/confirmation.html", context
            )
            text_message = render_to_string(
                "emails/subscription/confirmation.txt", context
            )

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=tenant_from_email(),
            to=[user.email],
            reply_to=[tenant_contact_email()],
        )
        email.attach_alternative(html_message, "text/html")
        email.send()

        logger.info(
            f"Sent confirmation email for subscription {subscription.id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to send confirmation email for subscription {subscription.id}: {e}"
        )
        return False


def check_subscription_before_send(user: "User", topic_slug: str) -> bool:
    return UserSubscription.objects.filter(
        user=user,
        topic__slug=topic_slug,
        status=UserSubscription.SubscriptionStatus.ACTIVE,
    ).exists()


def _make_unsubscribe_token(user: "AbstractBaseUser") -> str:
    """Sign the user's pk + owning schema into a tamper-proof token.

    The schema is baked in because ``SECRET_KEY`` is global and the
    user table exists in every tenant schema — a pk-only token minted
    for tenant A's user 42 would verify on tenant B's domain and
    silently unsubscribe tenant B's user 42. The verifier rejects any
    token whose schema does not match ``connection.schema_name``.
    """
    return signing.dumps(
        {"schema": connection.schema_name, "pk": user.pk},
        salt=UNSUBSCRIBE_SALT,
    )


def generate_unsubscribe_link(user: "User", topic: SubscriptionTopic) -> str:
    # The unsubscribe URL targets a Django API endpoint that has no
    # Nuxt proxy, so the tenant's API origin is the correct base. The
    # token bakes in ``connection.schema_name`` (see
    # ``_make_unsubscribe_token``) and the verifier rejects any token
    # whose schema doesn't match the request's — so a platform-host
    # link here is guaranteed rejected for every non-platform tenant.
    token = _make_unsubscribe_token(user)
    base_url = get_tenant_api_base_url()
    return f"{base_url}/api/v1/user/unsubscribe/{token}/{topic.slug}"


def generate_blanket_unsubscribe_link(
    user: "AbstractBaseUser",
) -> str:
    """Build the blanket (no-topic) unsubscribe URL for ``user``.

    Mirrors ``generate_unsubscribe_link`` but emits the URL form that
    ``UnsubscribeAllView`` accepts — drops every active subscription
    when the recipient hits one-click unsubscribe in Gmail/Outlook.

    Use this in marketing/notification emails that aren't bound to a
    single topic (re-engagement, product alerts) so the email still
    carries an RFC 8058-compliant ``List-Unsubscribe`` header.

    Accepts ``AbstractBaseUser`` so callers with a generic FK
    (e.g. ``ProductAlert.user``) can pass it directly — only ``pk`` is
    required.
    """
    # Django API endpoint — see note on ``generate_unsubscribe_link``.
    token = _make_unsubscribe_token(user)
    base_url = get_tenant_api_base_url()
    return f"{base_url}/api/v1/user/unsubscribe/{token}"


def _list_id_domain() -> str:
    """Bare hostname for the ``List-ID`` domain half.

    RFC 2919 requires the identifier to be ``list-label.domain`` enclosed
    in angle brackets, with no spaces or display names inside the
    brackets — ``tenant_site_name()`` (a human store name, potentially
    containing spaces/unicode) does not qualify. Uses the same tenant
    domain ``get_tenant_frontend_url`` resolves against, never a
    hardcoded platform domain.
    """
    return urlsplit(get_tenant_base_url()).netloc


def build_list_unsubscribe_headers(
    unsubscribe_url: str, *, list_id: str
) -> dict[str, str]:
    """Build the ``List-Unsubscribe`` + ``List-ID`` header dict.

    Centralised so every marketing/notification email emits the same
    Gmail/Yahoo-friendly shape (per RFC 8058 + 2024 sender rules):

    * ``List-Unsubscribe`` — both ``mailto:`` and HTTPS so MUAs that
      don't support one-click POST still have a working link.
    * ``List-Unsubscribe-Post`` — flags one-click as supported.
    * ``List-ID`` — opaque list identifier; helps mailbox providers
      bucket per-list deliverability stats.
    """
    return {
        "List-Unsubscribe": (
            f"<mailto:{tenant_contact_email()}?subject=unsubscribe>, "
            f"<{unsubscribe_url}>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-ID": f"<{list_id}.{_list_id_domain()}>",
    }


def build_transactional_list_headers(*, list_id: str) -> dict[str, str]:
    """Build the ``List-Unsubscribe`` + ``List-ID`` header dict for
    transactional emails (order receipts, payment failures, shipping
    notifications, invoices).

    The shopper can't really opt out of receipts for what they bought,
    but Gmail/Yahoo's 2024 bulk-sender rules expect a usable
    ``List-Unsubscribe`` even on transactional traffic — so we emit
    just the ``mailto:`` form. ``List-Unsubscribe-Post=One-Click`` is
    intentionally omitted: there's no programmatic unsubscribe path
    for transactional, and clients that see One-Click without a
    matching HTTPS endpoint penalise the sender. ``List-ID`` keeps
    per-stream deliverability stats clean at the mailbox provider.
    """
    return {
        "List-Unsubscribe": (
            f"<mailto:{tenant_contact_email()}?subject=unsubscribe>"
        ),
        "List-ID": f"<{list_id}.{_list_id_domain()}>",
    }


def get_user_subscription_summary(user: "User") -> dict[str, Any]:
    subscriptions = UserSubscription.objects.filter(user=user).select_related(
        "topic"
    )

    summary = {
        "total": subscriptions.count(),
        "active": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).count(),
        "pending": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.PENDING
        ).count(),
        "unsubscribed": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED
        ).count(),
        "by_category": {},
    }

    for subscription in subscriptions:
        category = subscription.topic.category
        if category not in summary["by_category"]:
            summary["by_category"][category] = {
                "total": 0,
                "active": 0,
            }

        summary["by_category"][category]["total"] += 1
        if subscription.status == UserSubscription.SubscriptionStatus.ACTIVE:
            summary["by_category"][category]["active"] += 1

    return summary
