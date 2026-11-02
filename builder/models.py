"""The page builder's data: documents, their versions, releases.

A **document** is the unit of editing and of publishing: the theme, a
section group (header, footer, ...), a template or a page the store
created. It holds a working copy (``draft``) that only the editor reads.
A **version** is an immutable snapshot of a document. A **release** is an
immutable map from document key to version, and it is the only thing the
public storefront reads, so publishing several documents is one row and a
rollback is a new row.

The structure of a document belongs to the storefront's block catalogue
(``builder.storefront``); Django never queries inside it. The columns
beside it (``block_types``, ``refs``, ``BuilderReference``) are derived at
write time so the questions Django does have, "which stores use this
block", "where is this product used", are indexed reads.

Staff are platform identities in the public schema, so every actor column
is a plain id read through ``StaffActor`` (``tenant.staff_actors``).
"""

from __future__ import annotations

import re
from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import (
    SoftDeleteManager,
    SoftDeleteModel,
    TimeStampMixinModel,
)
from core.utils.files import sanitize_filename
from tenant.staff_actors import StaffActor


class DocumentKind(models.TextChoices):
    THEME = "theme", _("Theme")
    GROUP = "group", _("Section group")
    TEMPLATE = "template", _("Template")
    PAGE = "page", _("Page")


#: ``theme``, ``group:<name>``, ``template:<name>``, ``page:<slug>``.
THEME_KEY = "theme"
_NAME = r"[a-z0-9][a-z0-9_-]{0,63}"
_KEY_PATTERNS: dict[DocumentKind, re.Pattern[str]] = {
    DocumentKind.THEME: re.compile(rf"^{THEME_KEY}$"),
    DocumentKind.GROUP: re.compile(rf"^group:{_NAME}$"),
    DocumentKind.TEMPLATE: re.compile(rf"^template:{_NAME}$"),
    DocumentKind.PAGE: re.compile(rf"^page:{_NAME}$"),
}


def kind_of_key(key: str) -> DocumentKind | None:
    """The kind a document key names, or ``None`` for a malformed key."""
    for kind, pattern in _KEY_PATTERNS.items():
        if pattern.match(key):
            return kind
    return None


def empty_document() -> dict[str, Any]:
    return {"sections": {}, "order": []}


class BuilderDocument(SoftDeleteModel, TimeStampMixinModel):
    """One editable document and its working copy.

    Only pages are ever deleted (soft); the theme, groups and templates
    are fixed keys. Written only through ``builder.services``.
    """

    kind = models.CharField(
        _("Kind"), max_length=16, choices=DocumentKind.choices
    )
    key = models.CharField(_("Key"), max_length=80, unique=True)
    draft = models.JSONField(
        _("Draft"),
        default=empty_document,
        help_text=_("The working copy: the default-language content."),
    )
    draft_i18n = models.JSONField(
        _("Draft translations"),
        default=dict,
        db_default={},
        blank=True,
        help_text=_(
            "Sparse per-language overlay: {locale: {node id: {setting "
            "pointer: value}}}. Structure is never per language."
        ),
    )
    draft_revision = models.PositiveIntegerField(
        _("Draft revision"),
        default=1,
        db_default=1,
        help_text=_(
            "Moves with every change of the draft; a save names the "
            "revision it was made against (If-Match)."
        ),
    )
    draft_catalogue_hash = models.CharField(
        _("Catalogue hash"),
        max_length=128,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "The storefront catalogue build the draft was last validated "
            "against."
        ),
    )
    published_revision = models.PositiveIntegerField(
        _("Published revision"),
        null=True,
        blank=True,
        help_text=_(
            "The draft revision the current release carries, empty when "
            "unknown (after a rollback, before a first publish). A draft "
            "with another revision has unpublished changes."
        ),
    )
    updated_by_staff_id = models.BigIntegerField(
        _("Updated by (staff)"),
        null=True,
        blank=True,
        help_text=_(
            "Id of the platform staff member who last saved the draft "
            "(public schema, not a store user)."
        ),
    )
    updated_by_staff = StaffActor()

    objects = SoftDeleteManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Builder document")
        verbose_name_plural = _("Builder documents")
        ordering = ["key"]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.key


class VersionKind(models.TextChoices):
    CHECKPOINT = "checkpoint", _("Named save")
    PUBLISH = "publish", _("Publish")


class _Immutable(models.Model):
    """A row that is written once. Retention deletes whole rows; nothing
    edits one."""

    class Meta(TypedModelMeta):
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} rows are immutable")
        super().save(*args, **kwargs)


class BuilderVersion(_Immutable):
    document = models.ForeignKey(
        BuilderDocument,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name=_("Document"),
    )
    number = models.PositiveIntegerField(_("Number"))
    kind = models.CharField(
        _("Kind"),
        max_length=16,
        choices=VersionKind.choices,
        default=VersionKind.CHECKPOINT,
        db_default=VersionKind.CHECKPOINT,
    )
    data = models.JSONField(_("Data"))
    i18n = models.JSONField(_("Translations"), default=dict, db_default={})
    block_types = ArrayField(
        models.CharField(max_length=80),
        default=list,
        db_default=[],
        verbose_name=_("Block types"),
        help_text=_("Every type@version used, for the catalogue usage gate."),
    )
    refs = models.JSONField(
        _("References"),
        default=dict,
        db_default={},
        help_text=_("{kind: {target id: [node pointers]}}"),
    )
    catalogue_hash = models.CharField(_("Catalogue hash"), max_length=128)
    content_hash = models.CharField(
        _("Content hash"), max_length=64, help_text=_("sha256 of data+i18n")
    )
    size_bytes = models.PositiveIntegerField(_("Size (bytes)"))
    created_by_staff_id = models.BigIntegerField(
        _("Created by (staff)"), null=True, blank=True
    )
    created_by_staff = StaffActor()
    note = models.CharField(_("Note"), max_length=255, blank=True, default="")
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Builder version")
        verbose_name_plural = _("Builder versions")
        ordering = ["document", "-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "number"],
                name="builder_version_unique_number",
            ),
        ]
        indexes = [
            GinIndex(fields=["block_types"], name="builder_version_blocks_gin"),
            models.Index(
                fields=["document", "content_hash"],
                name="builder_version_content_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document.key} v{self.number}"


class BuilderRelease(_Immutable):
    """What the public storefront reads: document key -> version id."""

    number = models.PositiveIntegerField(_("Number"), unique=True)
    versions = models.JSONField(
        _("Versions"), help_text=_("{document key: version id}")
    )
    published_by_staff_id = models.BigIntegerField(
        _("Published by (staff)"), null=True, blank=True
    )
    published_by_staff = StaffActor()
    published_at = models.DateTimeField(_("Published at"), default=timezone.now)
    note = models.CharField(_("Note"), max_length=255, blank=True, default="")
    rolled_back_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rollbacks",
        verbose_name=_("Rolled back to"),
        help_text=_("The release whose versions this one restores."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Builder release")
        verbose_name_plural = _("Builder releases")
        ordering = ["-number"]

    def __str__(self) -> str:
        return f"#{self.number}"


class ScheduleStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PUBLISHED = "published", _("Published")
    CANCELLED = "cancelled", _("Cancelled")
    FAILED = "failed", _("Failed")


class BuilderScheduledPublish(TimeStampMixinModel):
    """A publish waiting for its time.

    What will go live is frozen when it is scheduled: the drafts were
    validated then and are held as versions (``versions``), so the release
    made at ``scheduled_for`` is what the merchant previewed, whatever is
    edited meanwhile. At that time the references are checked again (a
    product can be deleted in between) and a schedule that can no longer be
    released is marked ``failed`` with the problems in ``failure``, never
    half done. Written only through ``builder.scheduling``.
    """

    scheduled_for = models.DateTimeField(_("Scheduled for"))
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=ScheduleStatus.choices,
        default=ScheduleStatus.PENDING,
        db_default=ScheduleStatus.PENDING,
    )
    versions = models.JSONField(
        _("Versions"), help_text=_("{document key: version id}")
    )
    revisions = models.JSONField(
        _("Revisions"),
        help_text=_("{document key: draft revision it was taken from}"),
    )
    note = models.CharField(_("Note"), max_length=255, blank=True, default="")
    created_by_staff_id = models.BigIntegerField(
        _("Scheduled by (staff)"), null=True, blank=True
    )
    created_by_staff = StaffActor()
    executed_at = models.DateTimeField(_("Executed at"), null=True, blank=True)
    release = models.ForeignKey(
        BuilderRelease,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="schedules",
        verbose_name=_("Release"),
    )
    failure = models.JSONField(
        _("Failure"),
        default=list,
        db_default=[],
        blank=True,
        help_text=_("Why it could not be released: [{path, code, message}]"),
    )

    acknowledged_at = models.DateTimeField(
        _("Acknowledged at"),
        null=True,
        blank=True,
        help_text=_(
            "When someone saw that it failed. A failed schedule shows on the "
            "store dashboard until then."
        ),
    )
    acknowledged_by_staff_id = models.BigIntegerField(
        _("Acknowledged by (staff)"), null=True, blank=True
    )
    acknowledged_by_staff = StaffActor()

    class Meta(TypedModelMeta):
        verbose_name = _("Scheduled publish")
        verbose_name_plural = _("Scheduled publishes")
        ordering = ["scheduled_for"]
        indexes = [
            # The mixin's ``%(class)s`` names are 37 characters here, past
            # the 32 an index name may have (models.E034).
            BTreeIndex(fields=["created_at"], name="builder_sched_created_ix"),
            BTreeIndex(fields=["updated_at"], name="builder_sched_updated_ix"),
            models.Index(
                fields=["status", "scheduled_for"],
                name="builder_sched_due_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scheduled_for:%Y-%m-%d %H:%M} ({self.status})"


def document_label(key: str) -> str:
    """``Template: home`` for ``template:home``: what a person reads."""
    kind = kind_of_key(key)
    if kind is None:
        return key
    name = key.partition(":")[2]
    return f"{kind.label}: {name}" if name else str(kind.label)


class ReferenceKind(models.TextChoices):
    PRODUCT = "product", _("Product")
    CATEGORY = "category", _("Category")
    PAGE = "page", _("Page")
    MEDIA = "media", _("Media")
    BLOG_CATEGORY = "blogCategory", _("Blog category")
    CONTENT_PAGE = "contentPage", _("Content page")
    BLOG_POST = "blogPost", _("Blog post")


class BuilderReference(models.Model):
    """What the CURRENT release points at, rebuilt on every release.

    Lets a product, category or page admin warn "used on: home, header"
    before a delete. Derived: never edited, never the source of truth.
    """

    kind = models.CharField(
        _("Kind"), max_length=16, choices=ReferenceKind.choices
    )
    target = models.CharField(_("Target"), max_length=64)
    document = models.CharField(_("Document"), max_length=80)
    node_path = models.CharField(_("Node path"), max_length=512)

    class Meta(TypedModelMeta):
        verbose_name = _("Builder reference")
        verbose_name_plural = _("Builder references")
        ordering = ["kind", "target", "document", "node_path"]
        indexes = [
            models.Index(
                fields=["kind", "target"], name="builder_ref_target_ix"
            ),
            models.Index(fields=["document"], name="builder_ref_document_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.target} in {self.document}"


def asset_upload_path(instance: MediaAsset, filename: str) -> str:
    """``uploads/builder/<name>`` in the store's media directory, which
    media-stream serves like every other upload."""
    return f"uploads/builder/{sanitize_filename(filename)}"


class MediaAsset(TranslatableModel, TimeStampMixinModel):
    """An image in the store's media library.

    A block's image setting stores this row's id (``ref:media``), never a
    URL, so the storefront knows the size to reserve before the file
    arrives (and to size ``srcset`` for), a translated ``alt``, and the
    focal point to crop around; and a used asset cannot disappear unseen.

    ``alt`` is a parler translation: the row stands alone and its text is
    one plain field, unlike a block's settings (a JSON overlay, because
    structure must not vary per language). Written only through
    ``builder.media_services``, which measures the file.
    """

    file = models.ImageField(
        _("File"), upload_to=asset_upload_path, max_length=255
    )
    width = models.PositiveIntegerField(
        _("Width"), editable=False, help_text=_("As a browser shows it, in px.")
    )
    height = models.PositiveIntegerField(
        _("Height"),
        editable=False,
        help_text=_("As a browser shows it, in px."),
    )
    mime = models.CharField(_("Type"), max_length=32, editable=False)
    size_bytes = models.PositiveIntegerField(_("Size (bytes)"), editable=False)
    focal_x = models.FloatField(
        _("Focal point X"),
        default=0.5,
        db_default=0.5,
        help_text=_("0 is the left edge, 1 the right."),
    )
    focal_y = models.FloatField(
        _("Focal point Y"),
        default=0.5,
        db_default=0.5,
        help_text=_("0 is the top edge, 1 the bottom."),
    )
    uploaded_by_staff_id = models.BigIntegerField(
        _("Uploaded by (staff)"), null=True, blank=True
    )
    uploaded_by_staff = StaffActor()

    translations = TranslatedFields(
        alt=models.CharField(
            _("Alt text"), max_length=255, blank=True, default=""
        )
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Media asset")
        verbose_name_plural = _("Media assets")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(focal_x__gte=0, focal_x__lte=1)
                & models.Q(focal_y__gte=0, focal_y__lte=1),
                name="builder_asset_focal_in_unit_square",
            ),
        ]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.file.name.rsplit("/", 1)[-1]
