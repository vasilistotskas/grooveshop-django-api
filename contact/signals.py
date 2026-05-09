import logging
import re

from django.db import transaction
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
    """Dispatch a contact-form notification email via Celery.

    Uses ``transaction.on_commit`` so the Celery task is only enqueued
    after the Contact row is committed — the task worker therefore always
    finds the row when it loads it by PK.
    """
    if not created:
        return

    from contact.tasks import send_contact_notification_email_task

    transaction.on_commit(
        lambda contact_id=instance.id: (
            send_contact_notification_email_task.delay(contact_id)
        )
    )
