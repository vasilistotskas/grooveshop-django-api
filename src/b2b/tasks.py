import logging

from b2b.emails import REVIEW_EMAIL_KEYS
from core import celery_app
from core.retry import MAIL_TRANSIENT_ERRORS
from core.tasks import MonitoredTask
from emails.send import send_email, was_sent

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_business_profile_status_email(self, profile_id: int) -> dict:
    """Notify the customer that their business profile was reviewed.

    Sends for APPROVED/REJECTED only — the current status is read at
    send time, so a rapid re-review supersedes an in-flight retry.
    """
    from b2b.models import BusinessProfile

    try:
        profile = BusinessProfile.objects.select_related(
            "user", "customer_group"
        ).get(id=profile_id)
    except BusinessProfile.DoesNotExist:
        logger.error("Business profile %s not found for email", profile_id)
        return {"status": "error", "reason": "not_found"}

    key = REVIEW_EMAIL_KEYS.get(profile.status)
    if key is None:
        return {"status": "skipped", "reason": f"status_{profile.status}"}

    deliveries = send_email(key, profile)
    if not was_sent(deliveries):
        return {"status": "skipped", "reason": deliveries[0].status}

    logger.info(
        "Sent business profile %s email for profile %s",
        profile.status,
        profile_id,
    )
    return {"status": "success", "profile_id": profile_id}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_admin_new_business_profile_email(self, profile_id: int) -> dict:
    """Notify the store operator that a B2B application awaits review.

    MERCHANT-facing alert (the ``send_admin_new_order_email`` pattern):
    goes to the tenant's contact email — without it, review latency is
    invisible until someone happens to open the admin. Skips silently
    when no contact email is configured.
    """
    from b2b.models import BusinessProfile

    try:
        profile = BusinessProfile.objects.select_related(
            "user", "customer_group"
        ).get(id=profile_id)
    except BusinessProfile.DoesNotExist:
        logger.error("Business profile %s not found for email", profile_id)
        return {"status": "error", "reason": "not_found"}

    deliveries = send_email("team.b2b_application", profile)
    if not was_sent(deliveries):
        return {"status": "skipped", "reason": deliveries[0].status}

    logger.info(
        "Merchant new-B2B-application email sent for profile %s", profile_id
    )
    return {"status": "success", "profile_id": profile_id}
