"""Celery tasks for the contact app.

Handles asynchronous delivery of contact-form notification emails to
site administrators.  Keeping this out of the signal handler means the
HTTP response is never blocked by an SMTP round-trip and transient mail
failures are automatically retried.
"""

import logging
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from contact.scanners import INFECTED, AttachmentScannerError
from core import celery_app
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    TRANSIENT_DB_ERRORS,
    TRANSIENT_ERRORS,
)
from core.tasks import MonitoredTask
from core.utils.email_context import build_email_context
from core.utils.i18n import store_language
from tenant.credentials import tenant_contact_email, tenant_from_email

logger = logging.getLogger(__name__)

_LINE_BREAKS = re.compile(r"[\r\n\t]+")


def _one_line(value: str) -> str:
    """Collapse line breaks and tabs, so free text can sit in a subject."""
    return _LINE_BREAKS.sub(" ", value).strip()[:200]


def _submitter_reply_to(address: str, *, log_extra: dict) -> list[str]:
    """``Reply-To`` for the store's copy of a submission: the submitter.

    A form submission is how a stranger reaches the store, so pressing
    reply must answer them. The address is validated before it reaches a
    header: anything that is not one bare, well-formed address (a line
    break, a display name, a second recipient) is dropped and the mail
    goes out without ``Reply-To``, rather than carrying text a stranger
    typed into a header.
    """
    if not address:
        return []
    try:
        validate_email(address)
    except ValidationError:
        logger.warning(
            "Submitter address is not a valid email address — sending the "
            "notification without Reply-To",
            extra=log_extra,
        )
        return []
    return [address]


def _send_store_notification(
    *,
    recipient: str,
    reply_to: list[str],
    subject: str,
    template: str,
    context: dict,
) -> None:
    """Send one rendered notification from the store to its own inbox.

    Must run inside ``translation.override`` for the language the
    ``subject`` and ``context`` were built in: both templates render here.
    """
    message = EmailMultiAlternatives(
        subject,
        render_to_string(f"{template}.txt", context),
        tenant_from_email(),
        [recipient],
        reply_to=reply_to,
    )
    message.attach_alternative(
        render_to_string(f"{template}.html", context), "text/html"
    )
    message.send(fail_silently=False)


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_contact_notification_email_task(contact_id: int) -> bool:
    """Tell the store that someone filled in its contact form.

    Loads the Contact row by PK and mails the active tenant's own contact
    inbox (``tenant_contact_email``), NOT ``settings.ADMINS``. The mail is
    for the store's staff, so it is rendered in the store's language
    (``store_language``) and carries the submitter's address as
    ``Reply-To``: replying answers the customer.

    Returns True on success, False when the contact no longer exists or
    the store has no contact address.
    """
    from contact.models import Contact

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
    # ADMINS list: on multi-tenant it puts each tenant's submissions in
    # their own inbox.
    recipient = tenant_contact_email()
    if not recipient:
        logger.warning(
            "send_contact_notification_email_task: no contact email "
            "configured for the active tenant — skipping",
            extra={"contact_id": contact_id},
        )
        return False

    # ``contact`` here is the row, not the package: the FK's
    # related_name. Files are never sent as mail attachments - the
    # bytes are anonymous uploads and may be unscanned, so they stay
    # in the private tree and staff fetch them from the admin. The
    # email only has to say they exist, or nobody looks.
    attachments = list(contact.attachments.all())

    language = store_language()
    with translation.override(language):
        _send_store_notification(
            recipient=recipient,
            reply_to=_submitter_reply_to(
                contact.email, log_extra={"contact_id": contact_id}
            ),
            subject=_("New contact form submission from {name}").format(
                name=_one_line(contact.name)
            ),
            template="emails/contact/contact_notification",
            context=build_email_context(
                language=language,
                contact=contact,
                attachments=attachments,
            ),
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
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def send_feedback_notification_email_task(feedback_id: int) -> bool:
    """Tell the store that someone left feedback.

    Same shape as the contact-form notification: the store's contact
    inbox, the store's language, and the submitter's address (when they
    gave one) as ``Reply-To``.

    Returns True on success, False when the feedback no longer exists
    or no recipient is configured for the active tenant.
    """
    from contact.models import Feedback

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

    language = store_language()
    with translation.override(language):
        _send_store_notification(
            recipient=recipient,
            reply_to=_submitter_reply_to(
                feedback.email, log_extra={"feedback_id": feedback_id}
            ),
            subject=_("New feedback ({rating}★ {category}) submission").format(
                rating=feedback.rating,
                category=feedback.get_category_display(),
            ),
            template="emails/contact/feedback_notification",
            context=build_email_context(
                language=language,
                feedback=feedback,
            ),
        )

    logger.info(
        "Feedback notification email sent for Feedback #%s",
        feedback_id,
        extra={"feedback_id": feedback_id},
    )
    return True


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(AttachmentScannerError,),
    retry_backoff=True,
    retry_jitter=True,
)
def scan_contact_attachment(self, attachment_id: int) -> str:
    """Run the configured scanner over one uploaded attachment.

    Off the request path on purpose: an engine takes seconds and holds
    memory, and a visitor waiting on a progress bar must not be
    waiting on a signature database.

    Retries only ``AttachmentScannerError`` — a transport failure is
    worth trying again, a verdict is not.

    The last attempt records ``ERROR`` and returns instead of
    re-raising, and that early return is the whole point: with
    ``exc`` supplied, ``Task.retry`` re-raises the ORIGINAL exception
    once the limit is passed rather than raising
    ``MaxRetriesExceededError``, so without this the run would end as
    a plain task failure and the row would sit on ``PENDING`` forever.
    ``ERROR`` is not downloadable either — a scan that never completed
    is not a clean one — but it SAYS so, in the admin, next to the
    file.
    """
    from contact.models import ContactAttachment
    from contact.scanners import ERROR, get_scanner

    attachment = ContactAttachment.objects.filter(pk=attachment_id).first()
    if attachment is None or not attachment.file:
        # Reaped or deleted between upload and scan — not an error.
        return "missing"

    scanner = get_scanner()
    try:
        with attachment.file.open("rb") as stream:
            verdict, detail = scanner.scan(stream, size=attachment.size)
    except AttachmentScannerError as exc:
        logger.warning(
            "Attachment %s scan failed on %s (attempt %s of %s): %s",
            attachment.uuid,
            scanner.code,
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
        )
        if self.request.retries >= self.max_retries:
            _record_scan(attachment, ERROR, str(exc)[:255])
            return ERROR
        # Back to ``autoretry_for``, which owns the backoff.
        raise

    if verdict == INFECTED:
        # Keep the ROW as the audit record; drop the bytes. Nobody
        # needs a copy of malware in the private tree to know an
        # infected file was sent, and staff must not be able to
        # retrieve it by any later mistake.
        attachment.file.delete(save=False)
    _record_scan(attachment, verdict, detail[:255])
    return verdict


def _record_scan(attachment, verdict: str, detail: str) -> None:

    attachment.scan_status = verdict
    attachment.scan_detail = detail
    attachment.scanned_at = timezone.now()
    attachment.save(
        update_fields=["scan_status", "scan_detail", "scanned_at", "file"]
    )


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def reap_unclaimed_attachments() -> int:
    """Delete uploads that were never attached to an enquiry.

    The two-step flow means a visitor can upload three files and close
    the tab. Those rows have ``contact=None`` and a
    ``claim_deadline``; past it they are abandoned bytes, and the
    private tree must not accumulate them.

    The bytes go with the row through
    ``contact.signals.delete_attachment_file``, so this only has to
    decide WHICH rows are abandoned.
    """
    from contact.models import ContactAttachment

    stale = ContactAttachment.objects.filter(
        contact__isnull=True, claim_deadline__lt=timezone.now()
    )
    reaped = 0
    for attachment in stale.iterator():
        attachment.delete()
        reaped += 1
    if reaped:
        logger.info("Reaped %s unclaimed contact attachments", reaped)
    return reaped


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def purge_expired_attachment_files() -> int:
    """Drop the BYTES of attachments past the merchant's window.

    The row survives with its name, size, checksum and scan verdict —
    an enquiry stays legible, and an operator can still see that three
    drawings came with it — while the storage it costs goes to zero.
    That is storage limitation (GDPR art. 5(1)(e)) without destroying
    the business record.

    The window is a merchant setting, so a store that must keep tender
    documents for a year says so without a deploy.
    """
    from contact.models import ContactAttachment
    from core.store_settings import store_setting

    days = int(store_setting("CONTACT_ATTACHMENTS_RETENTION_DAYS"))
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    expired = ContactAttachment.objects.filter(
        contact__isnull=False, created_at__lt=cutoff
    ).exclude(file="")
    purged = 0
    for attachment in expired.iterator():
        attachment.file.delete(save=False)
        attachment.save(update_fields=["file"])
        purged += 1
    if purged:
        logger.info("Purged bytes of %s expired contact attachments", purged)
    return purged


#: Rows deleted per statement, so the first run on a store with years of
#: backlog never loads them all into one deletion collector at once.
RETENTION_BATCH_SIZE = 500


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=TRANSIENT_DB_ERRORS,
    retry_backoff=True,
)
def purge_expired_contact_records() -> dict[str, int]:
    """Delete enquiries and feedback older than the store's retention period.

    One setting, ``CONTACT_RETENTION_DAYS``, covers both ``Contact`` and
    ``Feedback``: both are free text typed by a stranger and kept for no
    purpose beyond answering it (GDPR art. 5(1)(e) storage limitation).
    ``0`` keeps everything. Rows already older than the period when the
    setting first applies go on the first run.

    The deletes go through the ORM, never a raw statement, so deleting a
    ``Contact`` cascades to its ``ContactAttachment`` rows and
    ``contact.signals.delete_attachment_file`` removes their bytes.

    Runs per tenant through
    ``tenant.tasks.fanout_purge_expired_contact_records`` and reads the
    period from the ACTIVE schema's setting, so one store's period never
    reaches another store's rows.
    """
    from contact.models import Contact, Feedback
    from contact.utils import contact_retention_days

    days = contact_retention_days()
    if days <= 0:
        return {"contacts": 0, "feedback": 0}
    cutoff = timezone.now() - timedelta(days=days)

    counts = {
        "contacts": _delete_older_than(Contact, cutoff),
        "feedback": _delete_older_than(Feedback, cutoff),
    }
    if any(counts.values()):
        logger.info(
            "Purged %s contact enquiries and %s feedback entries older "
            "than %s days",
            counts["contacts"],
            counts["feedback"],
            days,
        )
    return counts


def _delete_older_than(model, cutoff) -> int:
    """Delete ``model`` rows created before ``cutoff``, in batches."""
    deleted = 0
    stale = model.objects.filter(created_at__lt=cutoff)
    while pks := list(
        stale.values_list("pk", flat=True)[:RETENTION_BATCH_SIZE]
    ):
        _, per_model = model.objects.filter(pk__in=pks).delete()
        deleted += per_model.get(model._meta.label, 0)
    return deleted
