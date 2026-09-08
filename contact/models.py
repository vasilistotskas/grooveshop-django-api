from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from contact.managers import ContactManager, FeedbackManager
from contact.storage import attachment_upload_to, private_attachment_storage
from core.models import TimeStampMixinModel, UUIDModel


class Contact(
    TimeStampMixinModel,
    UUIDModel,
):
    name = models.CharField(_("Name"), max_length=100)
    email = models.EmailField(_("Email"))
    message = models.TextField(_("Message"))
    # Optional context a B2B enquiry carries and a consumer one does
    # not. All three are blank-able because the platform's own form
    # asks for none of them: a merchant whose form does (an
    # engineering contractor quoting per project needs to know which
    # utility is asking, and to phone back) gets them structured
    # rather than buried in the message body.
    #
    # ``db_default``, not ``default``: the deploy applies migrations in
    # a PreSync hook, so the column exists while the PREVIOUS image is
    # still serving — and that image's INSERT does not list it. A
    # Django-level default is dropped from the DDL once the migration
    # ends, which would make every contact submission in the rollout
    # window a NOT NULL violation. Same reasoning as
    # ``BlogPost.click_score``.
    company = models.CharField(
        _("Company"), max_length=150, blank=True, db_default=""
    )
    phone = models.CharField(
        _("Phone"), max_length=30, blank=True, db_default=""
    )
    # FREE TEXT, not choices: the taxonomy belongs to the merchant's
    # own form ("Προσφορά έργου", "DeSET / ΑΠΕ", "Υποστήριξη"), which
    # is content in a page section, not a platform-wide enum. It is a
    # self-declared label on an anonymous submission and never an
    # authorization input.
    subject = models.CharField(
        _("Subject"), max_length=60, blank=True, db_default=""
    )

    objects: ContactManager = ContactManager()

    def __str__(self):
        return f"{self.name} <{self.email}>"

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(name={self.name}, email={self.email})"
        )

    class Meta(TypedModelMeta):
        verbose_name = _("Contact")
        verbose_name_plural = _("Contacts")
        ordering = ["-created_at"]
        indexes = [
            # Both halves of the parent's pair are earned here: the list
            # orders by created_at and paginates it, `date_hierarchy`
            # range-scans it, RecentContactFilter issues four
            # `created_at__gte` variants, and the admin exposes a
            # RangeDateTimeFilter on updated_at as well.
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["email"], name="contact_email_ix"),
        ]


class ContactAttachment(
    TimeStampMixinModel,
    UUIDModel,
):
    """A file a visitor attached to an enquiry.

    This is the platform's only ANONYMOUS upload: every other
    ``FileField`` is written by store staff through the admin. That
    single fact drives the whole design, and the parts of it that live
    outside this class are named here so the picture is not scattered:

    * ``contact.storage`` — a private per-tenant tree with no URL, and
      a stored path derived from this row's UUID rather than from the
      uploader's filename.
    * ``ContactAdmin.attachment_download_view`` — the ONLY reader,
      gated on the admin login AND this model's ``view`` permission,
      streaming as ``application/octet-stream`` with
      ``Content-Disposition: attachment`` and ``nosniff``. There is no
      public URL, signed or otherwise, and no API route: staff read an
      enquiry's files where they read the enquiry.
    * ``ContactAttachmentSerializer`` — the gate: the merchant's
      per-tenant limits (count, size, allowed types), a magic-byte
      sniff of the content, and a hard byte ceiling enforced while
      streaming.
    * ``contact.scanners`` — an optional engine. ``scan_status`` says
      plainly which of "clean", "not scanned" and "infected" applies,
      so an unscanned file is never mistaken for a cleared one.
    * ``contact.tasks`` — scanning off the request path, and the two
      sweeps that keep the tree bounded: unclaimed rows reaped after
      hours, claimed ones' BYTES dropped after the retention window
      while the enquiry itself is kept.

    ``contact`` is NULLABLE because the upload happens BEFORE the
    enquiry exists: the visitor picks files, watches them upload, and
    only then submits the form, which claims them by id. A row whose
    ``contact`` is still null after ``claim_deadline`` is an abandoned
    upload and is reaped with its bytes.

    Not part of ``user.services.gdpr``'s erasure path on purpose: an
    enquiry has no user account behind it (``Contact`` has no user FK),
    so a data-subject request against one is an admin action on that
    row, and the retention sweep is what bounds it otherwise.
    """

    class ScanStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending scan")
        CLEAN = "CLEAN", _("Clean")
        INFECTED = "INFECTED", _("Infected")
        ERROR = "ERROR", _("Scan failed")
        SKIPPED = "SKIPPED", _("Not scanned")

    id = models.BigAutoField(primary_key=True)
    contact = models.ForeignKey(
        "contact.Contact",
        related_name="attachments",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_("Contact"),
        help_text=_(
            "Null until the enquiry that claims this upload is submitted."
        ),
    )
    file = models.FileField(
        _("File"),
        storage=private_attachment_storage,
        upload_to=attachment_upload_to,
        max_length=255,
        help_text=_(
            "Stored in the per-tenant PRIVATE tree. Reachable only by "
            "streaming it through the staff-gated download view; it has "
            "no public URL."
        ),
    )
    #: The uploader's own filename — DATA, shown to staff and used for
    #: the download's filename. Never a path component: see
    #: ``contact.storage``.
    original_name = models.CharField(_("Original name"), max_length=255)
    #: SNIFFED from the leading bytes, never the client's declared
    #: Content-Type and never derived from the extension.
    content_type = models.CharField(_("Content type"), max_length=120)
    size = models.PositiveIntegerField(_("Size"), help_text=_("Bytes."))
    #: SHA-256 of the stored bytes: lets an operator prove a file is
    #: the one that was sent, and lets a re-scan be attributed.
    checksum = models.CharField(_("Checksum"), max_length=64, db_index=True)
    scan_status = models.CharField(
        _("Scan status"),
        max_length=10,
        choices=ScanStatus,
        db_default=ScanStatus.PENDING,
        default=ScanStatus.PENDING,
    )
    #: Which engine said so, and what it said.
    scan_detail = models.CharField(
        _("Scan detail"), max_length=255, blank=True, db_default=""
    )
    scanned_at = models.DateTimeField(_("Scanned at"), null=True, blank=True)
    #: An unclaimed row past this instant is an abandoned upload.
    claim_deadline = models.DateTimeField(_("Claim deadline"))

    def __str__(self):
        return f"{self.original_name} ({self.size} B)"

    class Meta(TypedModelMeta):
        verbose_name = _("Contact Attachment")
        verbose_name_plural = _("Contact Attachments")
        ordering = ["id"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            # The reaper's query: unclaimed rows past their deadline.
            BTreeIndex(
                fields=["claim_deadline"], name="contact_att_deadline_ix"
            ),
        ]

    def extension(self) -> str:
        """Extension for the stored path, from the SNIFFED type.

        Cosmetic — nothing on the server dispatches on it — but it
        makes the private tree readable, and deriving it from the
        sniffed type rather than from the uploader's filename keeps
        attacker-controlled text out of the path entirely.
        """
        import mimetypes

        return mimetypes.guess_extension(self.content_type or "") or ".bin"

    def is_downloadable(self) -> bool:
        """Whether staff may be handed the bytes.

        ``INFECTED`` never, ``PENDING``/``ERROR`` not yet — a verdict
        that never arrived is not a clean one. ``SKIPPED`` yes: no
        engine is configured, containment is the guarantee, and the
        admin says as much beside the row.
        """
        return self.scan_status in {
            self.ScanStatus.CLEAN,
            self.ScanStatus.SKIPPED,
        }


class FeedbackCategory(models.TextChoices):
    GENERAL = "general", _("General")
    WEBSITE = "website", _("Website & UX")
    PRODUCTS = "products", _("Products")
    DELIVERY = "delivery", _("Delivery")
    SUPPORT = "support", _("Customer support")
    OTHER = "other", _("Other")


class Feedback(
    TimeStampMixinModel,
    UUIDModel,
):
    name = models.CharField(_("Name"), max_length=100, blank=True, default="")
    email = models.EmailField(_("Email"), blank=True, default="")
    rating = models.PositiveSmallIntegerField(
        _("Rating"), validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    category = models.CharField(
        _("Category"),
        max_length=20,
        choices=FeedbackCategory.choices,
        default=FeedbackCategory.GENERAL,
    )
    message = models.TextField(_("Message"))

    objects: FeedbackManager = FeedbackManager()

    def __str__(self):
        name = self.name or str(_("Anonymous"))
        return f"{self.get_category_display()} · {self.rating}★ · {name}"

    class Meta(TypedModelMeta):
        verbose_name = _("Feedback")
        verbose_name_plural = _("Feedback")
        ordering = ["-created_at"]
        indexes = [
            # created_at only: the list orders by it and `date_hierarchy`
            # range-scans it. updated_at is shown in the admin but never
            # sorted or filtered, and an index nobody reads is a write
            # cost on every row.
            BTreeIndex(fields=["created_at"], name="feedback_created_at_ix"),
            BTreeIndex(fields=["category"], name="feedback_category_ix"),
            BTreeIndex(fields=["rating"], name="feedback_rating_ix"),
        ]
