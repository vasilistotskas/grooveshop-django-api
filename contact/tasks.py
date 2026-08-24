"""Celery tasks for the contact app.

Handles asynchronous delivery of contact-form notification emails to
site administrators.  Keeping this out of the signal handler means the
HTTP response is never blocked by an SMTP round-trip and transient mail
failures are automatically retried.
"""

import logging

from django.core.mail import send_mail

from core import celery_app
from core.tasks import MonitoredTask
from tenant.credentials import tenant_contact_email, tenant_from_email

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_contact_notification_email_task(contact_id: int) -> bool:
    """Send a contact-form notification email to site administrators.

    Loads the Contact row by PK, renders a plain-text notification, and
    dispatches it to every address listed in ``settings.ADMINS``.

    Subject and name fields are CRLF-sanitised at write time (see
    ``contact/signals.py::_sanitize_header_value``) but we guard here
    too so the task is safe even when called outside the signal path.

    Returns True on success, False when the contact no longer exists or
    no admin recipients are configured.
    """
    import re

    from contact.models import Contact

    _CRLF_RE = re.compile(r"[\r\n\t]+")

    def _sanitize(value: str) -> str:
        return _CRLF_RE.sub(" ", value).strip()[:200]

    try:
        contact = Contact.objects.get(id=contact_id)
    except Contact.DoesNotExist:
        logger.warning(
            "send_contact_notification_email_task: Contact #%s not found",
            contact_id,
            extra={"contact_id": contact_id},
        )
        return False

    # Route to the tenant's public contact inbox, not the platform
    # ADMINS list (H23 in MULTI_TENANT_AUDIT.md). On a single-tenant
    # deployment this resolves to the same address operators use
    # already; on multi-tenant it puts each tenant's submissions in
    # their own inbox.
    recipient = tenant_contact_email()
    if not recipient:
        logger.warning(
            "send_contact_notification_email_task: no contact email "
            "configured for the active tenant — skipping",
            extra={"contact_id": contact_id},
        )
        return False
    recipient_list = [recipient]

    safe_name = _sanitize(contact.name)
    subject = f"New Contact Form Submission from {safe_name}"
    message = (
        f"Name: {contact.name}\n"
        f"Email: {contact.email}\n"
        f"Message: {contact.message}"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=tenant_from_email(),
        recipient_list=recipient_list,
        fail_silently=False,
    )

    logger.info(
        "Contact notification email sent for Contact #%s",
        contact_id,
        extra={"contact_id": contact_id},
    )
    return True


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_feedback_notification_email_task(feedback_id: int) -> bool:
    """Send a feedback-submission notification email to the tenant.

    Loads the Feedback row by PK, renders a plain-text notification,
    and dispatches it to the active tenant's public contact inbox.

    Returns True on success, False when the feedback no longer exists
    or no recipient is configured for the active tenant.

    Subject/name are CRLF-sanitised the same way as the contact-form
    task (``contact/signals.py::_sanitize_header_value``) — the name
    is free-text and, while not currently interpolated into the
    subject, is guarded here so a future subject change stays safe by
    default.
    """
    import re

    from contact.models import Feedback

    _CRLF_RE = re.compile(r"[\r\n\t]+")

    def _sanitize(value: str) -> str:
        return _CRLF_RE.sub(" ", value).strip()[:200]

    try:
        feedback = Feedback.objects.get(id=feedback_id)
    except Feedback.DoesNotExist:
        logger.warning(
            "send_feedback_notification_email_task: Feedback #%s not found",
            feedback_id,
            extra={"feedback_id": feedback_id},
        )
        return False

    recipient = tenant_contact_email()
    if not recipient:
        logger.warning(
            "send_feedback_notification_email_task: no contact email "
            "configured for the active tenant — skipping",
            extra={"feedback_id": feedback_id},
        )
        return False
    recipient_list = [recipient]

    safe_name = _sanitize(feedback.name) or "Anonymous"
    category_display = feedback.get_category_display()
    subject = _sanitize(
        f"New Feedback ({feedback.rating}★ {category_display}) submission"
    )
    message = (
        f"Rating: {feedback.rating}/5\n"
        f"Category: {category_display}\n"
        f"Name: {safe_name}\n"
        f"Email: {feedback.email or '—'}\n"
        f"Message: {feedback.message}"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=tenant_from_email(),
        recipient_list=recipient_list,
        fail_silently=False,
    )

    logger.info(
        "Feedback notification email sent for Feedback #%s",
        feedback_id,
        extra={"feedback_id": feedback_id},
    )
    return True
