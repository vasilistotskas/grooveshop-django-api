import logging

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# Eager gettext over lazy for email subjects — the string must resolve
# INSIDE the recipient-language override (see order/tasks.py rationale).
from django.utils import translation
from django.utils.translation import gettext as _

from core import celery_app
from core.tasks import MonitoredTask
from core.utils.email_context import build_email_context
from core.utils.i18n import get_user_language
from tenant.credentials import (
    tenant_contact_email,
    tenant_from_email,
    tenant_site_name,
)

logger = logging.getLogger(__name__)

_STATUS_TEMPLATES = {
    "APPROVED": "emails/b2b/business_profile_approved",
    "REJECTED": "emails/b2b/business_profile_rejected",
}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
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

    template_base = _STATUS_TEMPLATES.get(profile.status)
    if template_base is None:
        return {"status": "skipped", "reason": f"status_{profile.status}"}

    recipient = profile.user.email
    if not recipient:
        return {"status": "skipped", "reason": "no_email"}

    context = build_email_context(profile=profile)
    with translation.override(get_user_language(profile.user)):
        if profile.status == "APPROVED":
            subject = _(
                "[{site}] Your business account has been approved"
            ).format(site=tenant_site_name())
        else:
            subject = _(
                "[{site}] Update on your business account application"
            ).format(site=tenant_site_name())
        text_content = render_to_string(f"{template_base}.txt", context)
        html_content = render_to_string(f"{template_base}.html", context)

    msg = EmailMultiAlternatives(
        subject,
        text_content,
        tenant_from_email(),
        [recipient],
        reply_to=[tenant_contact_email()],
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()

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
    autoretry_for=(Exception,),
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

    staff_email = tenant_contact_email()
    if not staff_email:
        logger.warning(
            "send_admin_new_business_profile_email: no contact email "
            "configured — skipping",
            extra={"profile_id": profile_id},
        )
        return {"status": "skipped", "reason": "no_contact_email"}

    context = build_email_context(profile=profile)
    text_content = render_to_string(
        "emails/b2b/admin_new_business_profile.txt", context
    )
    html_content = render_to_string(
        "emails/b2b/admin_new_business_profile.html", context
    )

    msg = EmailMultiAlternatives(
        f"New B2B application — {profile.company_name}",
        text_content,
        tenant_from_email(),
        [staff_email],
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()

    logger.info(
        "Merchant new-B2B-application email sent for profile %s", profile_id
    )
    return {"status": "success", "profile_id": profile_id}
