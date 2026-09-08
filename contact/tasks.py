"""Celery tasks for the contact app.

Handles asynchronous delivery of contact-form notification emails to
site administrators.  Keeping this out of the signal handler means the
HTTP response is never blocked by an SMTP round-trip and transient mail
failures are automatically retried.
"""

import logging

from django.core.mail import send_mail

from contact.scanners import INFECTED, AttachmentScannerError
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
    # ADMINS list. On a single-tenant
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
    # ``contact`` here is the row, not the package: the FK's
    # related_name. Files are never sent as mail attachments - the
    # bytes are anonymous uploads and may be unscanned, so they stay
    # in the private tree and staff fetch them from the admin. The
    # email only has to say they exist, or nobody looks.
    files = list(contact.attachments.all())
    if files:
        listed = "\n".join(
            f"  - {f.original_name} ({f.size} B, {f.get_scan_status_display()})"
            for f in files
        )
        message += (
            f"\n\nAttachments ({len(files)}) - download in the admin:\n{listed}"
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
    from django.utils import timezone

    attachment.scan_status = verdict
    attachment.scan_detail = detail
    attachment.scanned_at = timezone.now()
    attachment.save(
        update_fields=["scan_status", "scan_detail", "scanned_at", "file"]
    )


@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=(Exception,),
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
    from django.utils import timezone

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
    autoretry_for=(Exception,),
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
    from datetime import timedelta

    from django.utils import timezone
    from extra_settings.models import Setting

    from contact.models import ContactAttachment

    days = int(Setting.get("CONTACT_ATTACHMENTS_RETENTION_DAYS", default=90))
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
