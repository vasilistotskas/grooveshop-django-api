import logging
import re

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from contact.models import Contact, ContactAttachment, Feedback

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


@receiver(
    post_delete,
    sender=ContactAttachment,
    dispatch_uid="contact.delete_attachment_file",
)
def delete_attachment_file(sender, instance, **kwargs):
    """Drop the bytes when the row goes.

    Django deletes rows, not files: without this, every path that
    removes an attachment - the reaper, a cascade from a deleted
    enquiry, a staff delete in the admin - would leave the file behind
    in the private tree. That is the failure mode that makes "storage
    grew forever" impossible to explain a year later, and it is a
    model invariant rather than something four callers must each
    remember.

    The reverse ("bytes go, row stays") is deliberate and explicit
    instead: an INFECTED verdict and the retention sweep both keep the
    row as the record.
    """
    if instance.file:
        instance.file.delete(save=False)
