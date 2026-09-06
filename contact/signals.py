import logging
import re

from django.db.models.signals import post_save
from django.dispatch import receiver

from contact.models import Contact, Feedback

logger = logging.getLogger(__name__)

_CRLF_RE = re.compile(r"[\r\n\t]+")


def _sanitize_header_value(value: str) -> str:
    return _CRLF_RE.sub(" ", value).strip()[:200]


@receiver(
    post_save, sender=Contact, dispatch_uid="contact.send_email_notification"
)
def send_email_notification(sender, instance, created, **kwargs):
    """Dispatch a contact-form notification email via Celery.

    Queued through ``dispatch_on_commit`` so the task is only enqueued
    after the Contact row is committed — the worker therefore always finds
    the row when it loads it by PK — and so the tenant schema is stamped
    HERE rather than read when the hook fires, by which time the
    connection may have unwound back to ``public``.
    """
    if not created:
        return

    from contact.tasks import send_contact_notification_email_task
    from tenant.celery import dispatch_on_commit

    dispatch_on_commit(send_contact_notification_email_task, [instance.id])


@receiver(
    post_save,
    sender=Feedback,
    dispatch_uid="contact.send_feedback_email_notification",
)
def send_feedback_email_notification(sender, instance, created, **kwargs):
    """Dispatch a feedback-submission notification email via Celery.

    Queued through ``dispatch_on_commit`` so the task is only enqueued
    after the Feedback row is committed — the worker therefore always finds
    the row when it loads it by PK — and so the tenant schema is stamped
    HERE rather than read when the hook fires, by which time the
    connection may have unwound back to ``public``.
    """
    if not created:
        return

    from contact.tasks import send_feedback_notification_email_task
    from tenant.celery import dispatch_on_commit

    dispatch_on_commit(send_feedback_notification_email_task, [instance.id])
