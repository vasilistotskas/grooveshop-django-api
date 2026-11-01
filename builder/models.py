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
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import (
    SoftDeleteManager,
    SoftDeleteModel,
    TimeStampMixinModel,
)
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


class ReferenceKind(models.TextChoices):
    PRODUCT = "product", _("Product")
    CATEGORY = "category", _("Category")
    PAGE = "page", _("Page")
    MEDIA = "media", _("Media")
    MENU = "menu", _("Menu")


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
