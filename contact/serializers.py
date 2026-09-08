import hashlib
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from contact.attachments import (
    CLAIM_WINDOW_HOURS,
    AttachmentPolicy,
    sanitize_filename,
    sniff_content_type,
)
from contact.models import Contact, ContactAttachment, Feedback
from contact.tasks import scan_contact_attachment
from contact.utils import (
    sanitize_message,
    validate_contact_content,
    validate_feedback_content,
)
from tenant.celery import dispatch_on_commit

# Digits plus the punctuation phone numbers are written with, in any
# country: `+30 2310 924 440`, `(0030) 2310-924440 ext. 12` is not
# accepted — an extension goes in the message.
_PHONE_RE = re.compile(r"[0-9+()\-.\s]{5,30}")


class ContactAttachmentSerializer(
    serializers.ModelSerializer[ContactAttachment]
):
    """One file uploaded for a contact enquiry to claim."""

    # That docstring is the OpenAPI component's description, so it is
    # written for whoever reads the published contract. The design
    # notes belong here instead:
    #
    # The checks run cheapest-first — the merchant's switch, then the
    # declared size, then the content sniff — because there is no
    # point reading 25 MB to discover the store has attachments turned
    # off.
    #
    # The response carries the row's ``uuid``, which is the CAPABILITY
    # the submit step spends to claim the file (see
    # ``ContactWriteSerializer.attachment_ids``). It is unguessable, it
    # is only good until ``claim_deadline``, and it stops being good
    # the moment an enquiry claims it.

    file = serializers.FileField(write_only=True)

    #: Set by ``validate_file`` and read by ``create``. Per-request
    #: state on a per-request object: the sniff happens where the
    #: rejection has to happen, and the row must record the same
    #: verdict rather than sniff a second time.
    _sniffed_type: str = ""

    class Meta:
        model = ContactAttachment
        fields = (
            "uuid",
            "file",
            "original_name",
            "content_type",
            "size",
            "scan_status",
            "created_at",
        )
        read_only_fields = (
            "uuid",
            "original_name",
            "content_type",
            "size",
            "scan_status",
            "created_at",
        )

    def validate_file(self, upload):
        policy = AttachmentPolicy()
        if not policy.enabled:
            # Belt and braces: the view is gated too. A serializer
            # reachable from anywhere else must not become the hole.
            raise serializers.ValidationError(
                _("Attachments are not accepted.")
            )
        if not upload.size:
            raise serializers.ValidationError(_("The file is empty."))
        if upload.size > policy.max_bytes:
            raise serializers.ValidationError(
                _("The file is larger than the %(mb)s MB limit.")
                % {"mb": policy.max_megabytes}
            )
        sniffed = sniff_content_type(upload)
        if not policy.accepts(sniffed):
            raise serializers.ValidationError(
                _("That file type is not accepted. Allowed: %(types)s.")
                % {"types": ", ".join(policy.allowed_types)}
            )
        self._sniffed_type = sniffed or ""
        return upload

    def create(self, validated_data):
        """Hash the bytes, then hand the file to the private storage.

        Two passes, neither of which buffers the file in memory:

        1. ``_digest`` streams the upload in chunks to derive the
           checksum AND the true byte count. The size is re-derived
           rather than trusted from ``upload.size`` (which is the
           client's multipart framing) — a lying ``Content-Length``
           must not get to leave a 50 MB file behind a 25 MB check.
        2. ``FieldFile.save`` hands the upload itself to the storage,
           whose ``_save`` MOVES a ``TemporaryUploadedFile`` into
           place rather than copying it. Reading the bytes into a
           ``ContentFile`` first would defeat that and put the whole
           file in the worker's heap.
        """
        upload = validated_data.pop("file")
        policy = AttachmentPolicy()

        checksum, size = _digest(upload, ceiling=policy.max_bytes)
        attachment = ContactAttachment(
            original_name=sanitize_filename(upload.name),
            content_type=self._sniffed_type,
            size=size,
            checksum=checksum,
            claim_deadline=timezone.now() + timedelta(hours=CLAIM_WINDOW_HOURS),
        )
        # The name passed here is discarded by ``attachment_upload_to``,
        # which derives the stored path from the row's UUID; only its
        # extension survives, and that comes from the sniffed type.
        attachment.file.save(attachment.extension(), upload, save=False)
        attachment.save()

        dispatch_on_commit(scan_contact_attachment, [attachment.pk])
        return attachment


def _digest(upload, *, ceiling: int) -> tuple[str, int]:
    """``(sha256 hex, byte count)`` of an upload, streamed and capped.

    Raises once the count passes ``ceiling`` so an oversized body is
    refused before anything is written, and rewinds afterwards so the
    storage gets the whole file.
    """
    digest = hashlib.sha256()
    size = 0
    upload.seek(0)
    for chunk in upload.chunks():
        size += len(chunk)
        if size > ceiling:
            raise serializers.ValidationError(
                {
                    "file": _("The file is larger than the %(mb)s MB limit.")
                    % {"mb": ceiling // (1024 * 1024)}
                }
            )
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest(), size


class ContactWriteSerializer(serializers.ModelSerializer[Contact]):
    #: Uploads to attach, by the uuid each upload returned. Write-only
    #: and never echoed back: the ids are capability tokens for
    #: unclaimed rows, and an enquiry response is not the place to hand
    #: one out again.
    attachment_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
        # The store's own ``max_count`` is checked below, but a
        # DECLARED field's limits are fixed at class definition, when
        # no tenant is bound — so the structural ceiling goes here.
        # Without it DRF constructs every UUID in the list before the
        # per-tenant check can refuse the list's LENGTH.
        max_length=AttachmentPolicy.MAX_COUNT_CEILING,
    )

    #: Rows resolved by ``validate_attachment_ids`` for ``create`` to
    #: claim. Empty when the submission carried no ids.
    _attachments: list[ContactAttachment] = []

    class Meta:
        model = Contact
        fields = (
            "id",
            "name",
            "email",
            "message",
            "company",
            "phone",
            "subject",
            "attachment_ids",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def validate_attachment_ids(self, value):
        """Resolve the ids to UNCLAIMED, in-window rows of THIS tenant.

        Every failure mode collapses to the same message on purpose: a
        stranger poking at ids must not learn from the error whether an
        id exists, whether it is already claimed, or whether it has
        expired.
        """
        if not value:
            return []
        policy = AttachmentPolicy()
        if not policy.enabled:
            raise serializers.ValidationError(
                _("Attachments are not accepted.")
            )
        if len(value) > policy.max_count:
            raise serializers.ValidationError(
                _("At most %(count)s files.") % {"count": policy.max_count}
            )
        if len(set(value)) != len(value):
            raise serializers.ValidationError(_("Duplicate attachment."))

        # The queryset is tenant-scoped by the schema the connection is
        # bound to — there is no cross-tenant read to guard against
        # here, which is the whole point of schema-per-tenant.
        rows = list(
            ContactAttachment.objects.filter(
                uuid__in=value,
                contact__isnull=True,
                claim_deadline__gte=timezone.now(),
            )
        )
        if len(rows) != len(value):
            raise serializers.ValidationError(
                _(
                    "One of the files is no longer available. Please "
                    "re-attach it."
                )
            )
        self._attachments = rows
        return value

    def validate(self, attrs):
        name = attrs.get("name", "")
        email = attrs.get("email", "")
        message = attrs.get("message", "")

        validation_result = validate_contact_content(name, email, message)

        if not validation_result["valid"]:
            errors = validation_result["errors"]

            error_messages = []
            for field, error in errors.items():
                error_messages.append(f"{field}: {error}")

            if error_messages:
                raise serializers.ValidationError(", ".join(error_messages))

        attrs["message"] = sanitize_message(message)

        return attrs

    def create(self, validated_data):
        """Create the enquiry, then claim its uploads onto it.

        The claim is a conditional UPDATE inside the same transaction
        as the insert: two submissions racing on the same id leave one
        of them empty-handed rather than both pointing at one file, and
        a failure anywhere rolls the enquiry back with it.
        """
        validated_data.pop("attachment_ids", None)
        rows = self._attachments
        with transaction.atomic():
            contact = super().create(validated_data)
            if rows:
                claimed = ContactAttachment.objects.filter(
                    pk__in=[row.pk for row in rows], contact__isnull=True
                ).update(contact=contact)
                if claimed != len(rows):
                    raise serializers.ValidationError(
                        {
                            "attachment_ids": _(
                                "One of the files is no longer available. "
                                "Please re-attach it."
                            )
                        }
                    )
        return contact

    def validate_name(self, value: str) -> str:
        if len(value.strip()) < 2:
            raise serializers.ValidationError(
                _("Name must be at least 2 characters long.")
            )
        return value.strip()

    def validate_company(self, value: str) -> str:
        return sanitize_message(value)

    def validate_phone(self, value: str) -> str:
        """Digits and the punctuation a phone number is written with.

        Not a format check: an office number, a mobile, an
        international prefix and an extension are all valid here, and
        the platform serves more than one country. This only refuses
        the field being used as a second message body.
        """
        cleaned = sanitize_message(value)
        if cleaned and not _PHONE_RE.fullmatch(cleaned):
            raise serializers.ValidationError(
                _("Enter a phone number, using digits and + ( ) - only.")
            )
        return cleaned

    def validate_subject(self, value: str) -> str:
        return sanitize_message(value)

    def validate_message(self, value: str) -> str:
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Message must be at least 10 characters long.")
            )
        if len(value) > 5000:
            raise serializers.ValidationError(
                _("Message is too long. Maximum 5000 characters allowed.")
            )
        return value.strip()


class FeedbackWriteSerializer(serializers.ModelSerializer[Feedback]):
    class Meta:
        model = Feedback
        fields = (
            "id",
            "name",
            "email",
            "rating",
            "category",
            "message",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def validate(self, attrs):
        name = attrs.get("name", "")
        email = attrs.get("email", "")
        message = attrs.get("message", "")
        rating = attrs.get("rating")

        validation_result = validate_feedback_content(
            name, email, message, rating
        )

        if not validation_result["valid"]:
            errors = validation_result["errors"]

            error_messages = []
            for field, error in errors.items():
                error_messages.append(f"{field}: {error}")

            if error_messages:
                raise serializers.ValidationError(", ".join(error_messages))

        attrs["message"] = sanitize_message(message)
        attrs["name"] = name.strip()

        return attrs

    def validate_rating(self, value: int) -> int:
        if not 1 <= value <= 5:
            raise serializers.ValidationError(
                _("Rating must be between 1 and 5.")
            )
        return value
