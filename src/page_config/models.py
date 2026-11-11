from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.fields import TranslationsForeignKey
from parler.models import TranslatableModel, TranslatedFieldsModel

from core.fields.rich_text import RichTextField
from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.models import (
    PublishableModel,
    SeoTranslationModel,
    TimeStampMixinModel,
    UUIDModel,
)
from page_config.legal_guard import body_error, publication_error, rename_error

if TYPE_CHECKING:
    from typing import Self


class PublishedTranslatableQuerySet(TranslatableOptimizedQuerySet):
    """``published()`` for a translatable ``PublishableModel``.

    Mirrors ``core.models.PublishedQuerySet.published()`` — the base
    ``PublishableManager`` isn't parler-aware, so a translatable
    publishable model needs its own manager stack (matching
    ``blog.managers.post.BlogPostManager``) to keep ``.published()`` and
    ``.with_translations()`` composable.
    """

    def published(self) -> Self:
        now = timezone.now()
        return self.filter(
            Q(published_at__lte=now, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )


class ContentPageQuerySet(PublishedTranslatableQuerySet):
    """Optimized QuerySet for ContentPage."""

    def for_list(self) -> Self:
        return self.with_translations()

    def for_detail(self) -> Self:
        return self.for_list()


class ContentPageManager(TranslatableOptimizedManager):
    queryset_class = ContentPageQuerySet

    def get_queryset(self) -> ContentPageQuerySet:
        return ContentPageQuerySet(self.model, using=self._db)

    def for_list(self) -> ContentPageQuerySet:
        return self.get_queryset().for_list()

    def for_detail(self) -> ContentPageQuerySet:
        return self.get_queryset().for_detail()

    def published(self) -> ContentPageQuerySet:
        return self.get_queryset().published()


class ContentPage(
    TranslatableModel,
    TimeStampMixinModel,
    PublishableModel,
    UUIDModel,
):
    """Merchant-editable, translatable content page.

    Covers store-policy pages a merchant owns end-to-end (return
    policy, terms, privacy, FAQ, about, shipping info) — a plain
    slug + rich-text body, unlike a page-builder document (sections
    and blocks, for structured pages like the homepage).
    """

    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    # Which platform legal-text revision this page is known to include
    # (``page_config.legal_documents.LEGAL_TEXT_UPDATES``). Set by the
    # seed and by a rollout that rewrote untouched text, or by the
    # merchant marking an update reviewed; NULL is a page nobody has
    # reconciled, which the admin flags. Not editable: it records what
    # happened to the text, not a setting.
    legal_text_revision = models.PositiveSmallIntegerField(
        _("Platform legal text revision"),
        null=True,
        blank=True,
        editable=False,
    )

    objects: ContentPageManager = ContentPageManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Content Page")
        verbose_name_plural = _("Content Pages")
        ordering = ["slug"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
        ]

    def __str__(self) -> str:
        title = self.safe_translation_getter("title", any_language=True)
        return title or self.slug

    def clean(self) -> None:
        # A legal page stays published at its slug (page_config.legal_guard).
        super().clean()
        saved_slug = (
            ContentPage.objects.filter(pk=self.pk)
            .values_list("slug", flat=True)
            .first()
            if self.pk
            else None
        )
        renamed = rename_error(saved_slug, self.slug)
        if renamed:
            raise ValidationError(
                {"slug": ValidationError(renamed.message, code=renamed.code)}
            )
        refused = publication_error(
            self.slug,
            is_published=self.is_published,
            published_at=self.published_at,
        )
        if refused:
            field = (
                "is_published"
                if refused.code == "legal_page_unpublished"
                else "published_at"
            )
            raise ValidationError(
                {field: ValidationError(refused.message, code=refused.code)}
            )


# Suppression is a stubs artefact, not a real override conflict: the two
# bases declare ``save_base`` with signatures that django-stubs types
# differently, and ty reports the pair rather than a mismatch in the code
# written here (same as ``product.ProductTranslation``).
class ContentPageTranslation(  # ty: ignore[invalid-method-override]
    TranslatedFieldsModel, SeoTranslationModel
):
    master = TranslationsForeignKey(
        "page_config.ContentPage",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    body = RichTextField(_("Body"), blank=True, null=True)

    class Meta:
        app_label = "page_config"
        db_table = "page_config_contentpage_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Content Page Translation")
        verbose_name_plural = _("Content Page Translations")

    def __str__(self) -> str:
        title = self.title or gettext("Untitled")
        return f"{title} ({self.language_code})"

    def clean(self) -> None:
        # A legal page keeps a body in every served language
        # (page_config.legal_guard).
        super().clean()
        if self.master_id is None:
            return
        refused = body_error(self.master.slug, self.language_code, self.body)
        if refused:
            raise ValidationError(
                {"body": ValidationError(refused.message, code=refused.code)}
            )


class LegalDocumentUnavailableError(Exception):
    """A legal page cannot be accepted in a language: it is unpublished,
    missing, or has no body there.

    The storefront 404s such a page, so a shopper was shown a broken
    link instead of the text. Nothing can record that as accepted.
    """

    def __init__(self, slug: str, language_code: str):
        self.slug = slug
        self.language_code = language_code
        super().__init__(
            f"Legal page {slug!r} has no published text in {language_code!r}"
        )


def legal_text_hash(title: str, body: str) -> str:
    """SHA-256 of a legal text's title and body.

    Encoded as a JSON pair, so no other title and body produce the same
    input (a separator character could move between the two).
    """
    payload = json.dumps([title, body], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class LegalDocumentVersionManager(models.Manager["LegalDocumentVersion"]):
    def in_force(self, slug: str, language_code: str) -> LegalDocumentVersion:
        """The text of legal page *slug* a shopper reads in
        *language_code* now, as a version row.

        Exactly that language, as the storefront's legal routes render
        it: no fallback to another one, and only while the page is
        published as the storefront shows it (``published()``: a page
        scheduled for later is hidden). The row is created the first
        time a text is accepted and reused while the text stays the
        same; an edit to the page makes the next acceptance a new
        version.
        """
        translation = (
            ContentPageTranslation.objects.select_related("master")
            .filter(
                master__in=ContentPage.objects.published(),
                master__slug=slug,
                language_code=language_code,
            )
            .first()
        )
        if translation is None or not (translation.body or "").strip():
            raise LegalDocumentUnavailableError(slug, language_code)
        title = translation.title
        body = translation.body
        version, _created = self.get_or_create(
            page=translation.master,
            language_code=language_code,
            content_hash=legal_text_hash(title, body),
            defaults={"title": title, "body": body},
        )
        return version


class LegalDocumentVersion(models.Model):
    """The exact text of a legal page in one language, as it was accepted.

    Never edited: a merchant may rewrite the page at any time, so what an
    order was placed under is a snapshot of the title and body, keyed by
    their SHA-256. One row per distinct text, shared by every acceptance
    of it. The page is protected while versions of it exist, so the
    record of what shoppers accepted cannot be deleted with it.
    """

    page = models.ForeignKey(
        ContentPage,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name=_("Page"),
    )
    language_code = models.CharField(_("Language"), max_length=15)
    title = models.CharField(_("Title"), max_length=255, blank=True)
    body = models.TextField(_("Body"))
    content_hash = models.CharField(
        _("Content hash"),
        max_length=64,
        help_text=_("SHA-256 of the title and body, as accepted."),
    )
    created_at = models.DateTimeField(_("First accepted at"), auto_now_add=True)

    objects: LegalDocumentVersionManager = LegalDocumentVersionManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Legal document version")
        verbose_name_plural = _("Legal document versions")
        ordering = ["page", "language_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "language_code", "content_hash"],
                name="legal_document_version_unique_text",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.page.slug} ({self.language_code}) {self.content_hash[:12]}"
        )
