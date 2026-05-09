"""Celery tasks for the contact app.

Handles asynchronous delivery of contact-form notification emails to
site administrators.  Keeping this out of the signal handler means the
HTTP response is never blocked by an SMTP round-trip and transient mail
failures are automatically retried.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

from core import celery_app
from core.tasks import MonitoredTask

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

    recipient_list = [admin[1] for admin in getattr(settings, "ADMINS", [])]
    if not recipient_list:
        logger.warning(
            "send_contact_notification_email_task: no ADMINS configured — skipping",
            extra={"contact_id": contact_id},
        )
        return False

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
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=False,
    )

    logger.info(
        "Contact notification email sent for Contact #%s",
        contact_id,
        extra={"contact_id": contact_id},
    )
    return True
