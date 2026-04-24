import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver

from contact.models import Contact

logger = logging.getLogger(__name__)

_CRLF_RE = re.compile(r"[\r\n\t]+")


def _sanitize_header_value(value: str) -> str:
    return _CRLF_RE.sub(" ", value).strip()[:200]


@receiver(
    post_save, sender=Contact, dispatch_uid="contact.send_email_notification"
)
def send_email_notification(sender, instance, created, **kwargs):
    if not created:
        return

    safe_name = _sanitize_header_value(instance.name)
    subject = f"New Contact Form Submission from {safe_name}"
    message = (
        f"Name: {instance.name}\n"
        f"Email: {instance.email}\n"
        f"Message: {instance.message}"
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [admin[1] for admin in getattr(settings, "ADMINS", [])]

    if not recipient_list:
        logger.warning("No admin recipient found in settings.ADMINS")
        return

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
            fail_silently=False,
        )
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
