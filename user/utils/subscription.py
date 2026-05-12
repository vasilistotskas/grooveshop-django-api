from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from datetime import timedelta

from extra_settings.models import Setting
from django.conf import settings

from core.utils.tenant_urls import get_tenant_base_url, get_tenant_frontend_url
from tenant.credentials import tenant_contact_email, tenant_from_email
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from core.utils.i18n import get_user_language

from user.models.subscription import SubscriptionTopic, UserSubscription

logger = logging.getLogger(__name__)

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
        # build against the API origin. In single-tenant deployments
        # this is the tenant's only API URL; for multi-tenant the
        # follow-up is either a per-tenant ``get_tenant_api_base_url``
        # helper (api.<tenant-domain>) or a Nuxt proxy route that
        # forwards to Django — see H1 in MULTI_TENANT_AUDIT.md.
        api_base = settings.API_BASE_URL.rstrip("/")
        # If the stored value already looks like an absolute URL (legacy
        # rows from before this fix), respect it as-is so existing tenants
        # aren't broken until they run backfill_extra_settings_defaults.
        if url_path_template and url_path_template.startswith("http"):
            raw_confirmation_url = url_path_template
        else:
            raw_confirmation_url = f"{api_base}{url_path_template}"
        confirmation_url = raw_confirmation_url.format(
            token=subscription.confirmation_token
        )

        user = subscription.user
        language = get_user_language(user)

        context = {
            "user": user,
            "topic": subscription.topic,
            "subscription": subscription,
            "confirmation_url": confirmation_url,
            "SITE_NAME": settings.SITE_NAME,
            "SITE_URL": get_tenant_base_url(),
            "INFO_EMAIL": tenant_contact_email(),
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            "LANGUAGE_CODE": language,
        }

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


def generate_unsubscribe_link(user: "User", topic: SubscriptionTopic) -> str:
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    # See note in ``send_subscription_confirmation`` — the unsubscribe
    # URL targets a Django API endpoint that has no Nuxt proxy, so the
    # API origin is the correct base. Multi-tenant follow-up tracked
    # against H1 in MULTI_TENANT_AUDIT.md.
    base_url = settings.API_BASE_URL.rstrip("/")
    unsubscribe_url = (
        f"{base_url}/api/v1/user/unsubscribe/{uid}/{token}/{topic.slug}"
    )

    return unsubscribe_url


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
    (e.g. ``ProductAlert.user``) can pass it directly — only ``pk`` and
    ``default_token_generator`` compatibility are required.
    """
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    # Django API endpoint — see note above. H1 in MULTI_TENANT_AUDIT.md.
    base_url = settings.API_BASE_URL.rstrip("/")
    return f"{base_url}/api/v1/user/unsubscribe/{uid}/{token}"


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
        "List-ID": f"{list_id}.{settings.SITE_NAME}",
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
        "List-ID": f"{list_id}.{settings.SITE_NAME}",
    }


def send_newsletter(
    topic: SubscriptionTopic,
    subject: str,
    template_base: str,
    context: dict[str, Any],
    batch_size: int = 100,
    force: bool = False,
    dedup_window_hours: int = 24,
) -> dict[str, int]:
    """Send a newsletter to every ACTIVE subscriber of `topic`.

    The HTML/TXT body is rendered per-user under `translation.override(user.language_code)`;
    subject is caller-resolved (translate it in the caller if needed).

    Dedup: a Redis key `newsletter:sent:{slug}:{uid}:{date}` holds a flag for
    `dedup_window_hours`; set `force=True` to bypass (e.g. intentional re-send
    after a content fix).
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    active_subscriptions = UserSubscription.objects.filter(
        topic=topic, status=UserSubscription.SubscriptionStatus.ACTIVE
    ).select_related("user")

    today = timezone.now().date().isoformat()
    dedup_ttl_seconds = int(timedelta(hours=dedup_window_hours).total_seconds())

    for subscription in active_subscriptions.iterator(chunk_size=batch_size):
        user = subscription.user

        if not user.is_active or not user.email:
            stats["skipped"] += 1
            continue

        cache_key = f"newsletter:sent:{topic.slug}:{user.pk}:{today}"
        if not force and cache.get(cache_key):
            stats["skipped"] += 1
            continue

        unsubscribe_url = generate_unsubscribe_link(user, topic)
        user_context = context.copy()
        user_context.update(
            {
                "user": user,
                "topic": topic,
                "subscription": subscription,
                "unsubscribe_url": unsubscribe_url,
                "preferences_url": get_tenant_frontend_url(
                    "/account/subscriptions/"
                ),
                "SITE_NAME": settings.SITE_NAME,
                "SITE_URL": get_tenant_base_url(),
                "INFO_EMAIL": tenant_contact_email(),
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }
        )

        try:
            with translation.override(get_user_language(user)):
                html_message = render_to_string(
                    f"{template_base}.html", user_context
                )
                text_message = render_to_string(
                    f"{template_base}.txt", user_context
                )

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=tenant_from_email(),
                to=[user.email],
                reply_to=[tenant_contact_email()],
                headers=build_list_unsubscribe_headers(
                    unsubscribe_url, list_id=topic.slug
                ),
            )
            email.attach_alternative(html_message, "text/html")
            email.send()

            cache.set(cache_key, True, timeout=dedup_ttl_seconds)
            stats["sent"] += 1

        except Exception as e:
            logger.error(f"Failed to send newsletter to {user.email}: {e}")
            stats["failed"] += 1

    logger.info(
        f"Newsletter sent for topic {topic.slug}: "
        f"{stats['sent']} sent, {stats['failed']} failed, {stats['skipped']} skipped"
    )

    return stats


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
