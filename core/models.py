from __future__ import annotations

import uuid
import zlib
from typing import Any

from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.models import F, Func, JSONField, Max, Q, Value
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.cache.models import CachePurgeLog  # noqa: F401
from core.fields.plain_text import PlainTextField


class JSONBConcat(Func):
    """Postgres jsonb ``||`` — a shallow merge of the right into the left.

    Django has no built-in for the operator. Rendering it as an
    expression keeps the merge server-side, which is the whole point:
    the row is never read into Python first, so there is no window in
    which another writer's keys can be lost.

    Shallow by design, matching ``dict.update``: a nested object on both
    sides is replaced, not deep-merged.
    """

    arg_joiner = " || "
    template = "%(expressions)s"
    output_field = JSONField()


class SeoModel(models.Model):
    """
    Abstract model that adds SEO fields (title, description, keywords).
    """

    seo_title = models.CharField(
        _("Seo Title"), max_length=70, blank=True, default=""
    )
    # PlainTextField, not TextField: this lands verbatim inside
    # ``<meta name="description" content="...">``, where markup cannot
    # render and only corrupts the snippet.
    seo_description = PlainTextField(
        _("Seo Description"), max_length=300, blank=True, default=""
    )
    seo_keywords = models.CharField(
        _("Seo Keywords"), max_length=255, blank=True, default=""
    )

    class Meta:
        abstract = True


class SortableModel(models.Model):
    """
    Abstract model that adds a sort_order field and methods for moving items up/down.

    Concurrent appends are serialised with a transaction-scoped advisory
    lock; ``_lock_ordering_scope`` records why the row lock that used to
    stand there could not have worked.
    """

    sort_order = models.IntegerField(_("Sort Order"), null=True)

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["sort_order"], name="%(class)s_sort_order_ix"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Save the model instance, automatically assigning sort_order for new instances.

        Concurrent appends are serialised; see ``_lock_ordering_scope``.
        """
        if self.pk is None:
            with transaction.atomic():
                self._lock_ordering_scope()
                existing_max = self.get_max_sort_order(
                    self.get_ordering_queryset()
                )
                self.sort_order = (
                    0 if existing_max is None else existing_max + 1
                )
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def _lock_ordering_scope(self) -> None:
        """Serialise concurrent appends to this model's ordering.

        What stood here was ``get_ordering_queryset().select_for_update()``
        under the comment "Lock the table to prevent race conditions",
        and it locked nothing: Django DROPS the lock when the queryset
        is consumed by ``aggregate()``. Measured on the same queryset —
        listed, it emits ``... LIMIT 1 FOR UPDATE``; aggregated, it
        emits ``SELECT MAX("sort_order") ... FROM ...`` with no
        ``FOR UPDATE`` at all. Postgres forbids ``FOR UPDATE`` with an
        aggregate, so no arrangement of that call could have locked.

        Row locks would not have fixed it either: two concurrent creates
        contend over a row that does not exist yet, which is a phantom,
        and READ COMMITTED row locks do not prevent phantoms.
        Reproduced against the real database with two threads —
        ``sort_order`` came back ``[0, 0]`` on two runs out of three —
        and the duplicate is not cosmetic: ``move_up`` then raises
        ``MultipleObjectsReturned`` from its ``get()``.

        A transaction-scoped advisory lock is the narrowest thing that
        does work. It is released at COMMIT, so it can never be leaked
        to a pooled connection; it blocks nothing except another append
        to the same model; and the key carries the schema name because
        advisory locks are per DATABASE while every tenant shares one.
        The key is hashed in Python rather than with Postgres'
        ``hashtext`` so the value does not depend on a function that is
        an undocumented internal.
        """
        scope = f"{connection.schema_name}:{self._meta.label_lower}"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [zlib.crc32(scope.encode())],
            )

    def get_ordering_queryset(self) -> models.QuerySet[SortableModel]:
        """
        Get the queryset used for ordering operations.

        Override this method to customize ordering scope (e.g., per category).

        Returns:
            QuerySet of all instances to consider for ordering
        """
        model_class = self.__class__
        return model_class._default_manager.all()

    @staticmethod
    def get_max_sort_order(qs: models.QuerySet[SortableModel]) -> int | None:
        """
        Get the maximum sort_order value from a queryset.

        Args:
            qs: QuerySet to check for maximum sort_order

        Returns:
            Maximum sort_order value, or None if queryset is empty
        """
        return qs.aggregate(Max("sort_order"))["sort_order__max"]

    def move_up(self) -> None:
        """
        Move this item up in the sort order (decrease sort_order by 1).

        Swaps sort_order with the previous item in a transaction.

        Picks the nearest preceding item rather than requiring exactly
        one at ``sort_order - 1``. The old ``get()`` raised
        ``MultipleObjectsReturned`` — a 500 on the admin's move-up
        action — the moment two rows shared a position, which is what
        the unlocked append produced, and repairing the append does not
        repair rows already stored that way. It also skips a gap, which
        ``delete()``'s renumbering is supposed to prevent but a raw
        ``sort_order`` edit can still leave.
        """
        if self.sort_order is not None and self.sort_order > 0:
            with transaction.atomic():
                qs = self.get_ordering_queryset().select_for_update()
                prev_item = (
                    qs.filter(sort_order__lt=self.sort_order)
                    .order_by("-sort_order")
                    .first()
                )
                if prev_item is not None:
                    prev_item.sort_order, self.sort_order = (
                        self.sort_order,
                        prev_item.sort_order,
                    )
                    prev_item.save(update_fields=["sort_order"])
                    self.save(update_fields=["sort_order"])

    def move_down(self) -> None:
        """
        Move this item down in the sort order (increase sort_order by 1).

        Swaps sort_order with the next item in a transaction.

        Orders explicitly rather than leaning on the subclass's
        ``Meta.ordering``: ``first()`` without it returns whichever row
        the database felt like, which is only the nearest neighbour by
        coincidence.
        """
        if self.sort_order is not None:
            with transaction.atomic():
                qs = self.get_ordering_queryset().select_for_update()
                next_item = (
                    qs.filter(sort_order__gt=self.sort_order)
                    .order_by("sort_order")
                    .first()
                )
                if next_item:
                    next_item.sort_order, self.sort_order = (
                        self.sort_order,
                        next_item.sort_order,
                    )
                    next_item.save(update_fields=["sort_order"])
                    self.save(update_fields=["sort_order"])

    @transaction.atomic
    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """
        Delete the instance and reorder remaining items.

        Decrements sort_order for all items after this one to maintain
        continuous ordering.

        Returns:
            Tuple of (number of objects deleted, dict of deletions per type)
        """
        if self.sort_order is not None:
            qs = self.get_ordering_queryset().select_for_update()
            qs.filter(sort_order__gt=self.sort_order).update(
                sort_order=F("sort_order") - 1
            )
        return super().delete(*args, **kwargs)


class TimeStampMixinModel(models.Model):
    """
    Abstract model that adds created_at and updated_at timestamps.
    """

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["created_at"], name="%(class)s_created_at_ix"),
            BTreeIndex(fields=["updated_at"], name="%(class)s_updated_at_ix"),
        ]


class UUIDModel(models.Model):
    """
    Abstract model that adds a unique UUID field.
    """

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta(TypedModelMeta):
        abstract = True


class PublishedQuerySet(models.QuerySet):
    """
    QuerySet for PublishableModel that provides a published() filter.
    """

    def published(self):
        today = timezone.now()
        return self.filter(
            Q(published_at__lte=today, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )


PublishableManager = models.Manager.from_queryset(PublishedQuerySet)


class PublishableModel(models.Model):
    """
    Abstract model that adds published_at and is_published fields.
    """

    published_at = models.DateTimeField(
        _("Published At"), null=True, blank=True
    )
    is_published = models.BooleanField(_("Is Published"), default=False)

    objects = PublishableManager()

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(
                fields=["published_at"], name="%(class)s_published_at_ix"
            ),
            BTreeIndex(
                fields=["is_published"], name="%(class)s_is_published_ix"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class MetaDataModel(models.Model):
    """
    Abstract model that adds private_metadata and metadata JSON fields.
    """

    private_metadata = JSONField(
        blank=True, default=dict, encoder=DjangoJSONEncoder
    )
    metadata = JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)

    class Meta(TypedModelMeta):
        indexes = [
            GinIndex(fields=["private_metadata"], name="%(class)s_p_meta_ix"),
            GinIndex(fields=["metadata"], name="%(class)s_meta_ix"),
        ]
        abstract = True

    def save(self, *args, **kwargs):
        if not self.private_metadata:
            self.private_metadata = {}
        if not self.metadata:
            self.metadata = {}
        super().save(*args, **kwargs)

    def store_value_in_metadata(self, items: dict):
        """Merge ``items`` into ``metadata`` with a set-based UPDATE.

        ``metadata`` is ONE jsonb column, so writing it back from this
        instance would replace the whole document — losing every key
        another transaction committed since the instance was loaded.
        Postgres' ``||`` merges server-side instead, against whatever
        the row holds at UPDATE time, so only the keys named here move.

        This is not theoretical: ``POST /api/v1/loyalty/redeem`` loads
        the order with a bare ``.get()`` — no ``select_for_update``, no
        surrounding atomic — validates, and only then writes. A Viva
        checkout session minted in that window appends
        ``viva_order_codes``, which is how the webhook later finds the
        order; clobbering it strands a PAID order as unmatched until the
        auto-cancel sweep cancels it.
        """
        if not items:
            return
        type(self)._base_manager.filter(pk=self.pk).update(
            metadata=JSONBConcat(
                F("metadata"),
                Value(items, JSONField(encoder=DjangoJSONEncoder)),
            )
        )
        # Keep the in-memory copy usable — callers go on to set more
        # keys on it (see ``order/services.py`` right after
        # ``redeem_points``). It reflects this write, not any concurrent
        # one; ``refresh_from_db`` if you need the row's full state.
        self.metadata.update(items)


class SoftDeleteMixin(models.Model):
    """
    Abstract model that adds soft delete functionality.

    Provides soft delete capability where records are marked as deleted
    rather than being removed from the database.
    """

    deleted_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta(TypedModelMeta):
        abstract = True

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        """
        Soft delete the instance by marking it as deleted.

        Uses update_fields for better performance instead of full model save.

        Args:
            using: Database alias to use
            keep_parents: Whether to keep parent records (unused in soft delete)

        Returns:
            Tuple of (count, {model_label: count}) to match Django's delete signature
        """
        self.deleted_at = timezone.now()
        self.is_deleted = True
        self.save(update_fields=["deleted_at", "is_deleted"])
        return (1, {self._meta.label: 1})

    def restore(self) -> None:
        """
        Restore a soft-deleted instance.

        Uses update_fields for better performance.
        """
        self.deleted_at = None
        self.is_deleted = False
        self.save(update_fields=["deleted_at", "is_deleted"])


class SoftDeleteQuerySet(models.QuerySet):
    """
    QuerySet that overrides delete() to perform soft delete.

    Provides methods for soft delete, restore, and hard delete operations.
    """

    def delete(self) -> tuple[int, dict[str, int]]:
        """
        Soft delete all instances in the queryset.

        Returns:
            Tuple of (number updated, dict of updates per model)
        """
        count = super().update(deleted_at=timezone.now(), is_deleted=True)
        return (count, {self.model._meta.label: count})

    def restore(self) -> int:
        """
        Restore all soft-deleted instances in the queryset.

        Returns:
            Number of instances restored
        """
        return super().update(deleted_at=None, is_deleted=False)

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """
        Permanently delete all instances in the queryset.

        Returns:
            Tuple of (number deleted, dict of deletions per model)
        """
        return super().delete()


class SoftDeleteManager(models.Manager):
    """
    Manager that filters out soft-deleted items by default.

    Provides methods to access all records including deleted ones,
    or only deleted records.
    """

    def get_queryset(self) -> SoftDeleteQuerySet:
        """
        Get the default queryset excluding soft-deleted items.

        Returns:
            QuerySet with is_deleted=False filter applied
        """
        return SoftDeleteQuerySet(self.model, using=self._db).exclude(
            is_deleted=True
        )

    def all_with_deleted(self) -> SoftDeleteQuerySet:
        """
        Get all records including soft-deleted ones.

        Returns:
            Unfiltered QuerySet
        """
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self) -> SoftDeleteQuerySet:
        """
        Get only soft-deleted records.

        Returns:
            QuerySet with is_deleted=True filter applied
        """
        return SoftDeleteQuerySet(self.model, using=self._db).filter(
            is_deleted=True
        )


class SoftDeleteModel(SoftDeleteMixin, models.Model):
    class Meta(TypedModelMeta):
        abstract = True


class Translation(models.Model):
    """User-editable UI msgstr values backed by Postgres.

    Rosetta edits are mirrored into this table via the
    rosetta.signals.entry_changed receiver. On pod boot and on every
    cross-pod invalidation tick, core.rosetta_storage.apply_db_overlay
    reads from this table and overlays the values onto Django's
    in-memory gettext catalog, making the DB the durable source of
    truth for translations across deploys.

    Plural handling:
      - Singular entries use plural_index=0 and an empty msgid_plural.
      - Plural entries (from ngettext / msgid_plural in .po) use one row
        per plural form, with plural_index matching the msgstr[N] slot.

    Context-qualified msgids (pgettext) are stored as
    "<context>\\x04<msgid>" to match gettext's catalog key format, so
    no extra column is needed.
    """

    language_code = models.CharField(
        _("Language Code"),
        max_length=10,
    )
    msgid = models.TextField(_("Message ID"))
    msgid_plural = models.TextField(
        _("Plural Message ID"),
        blank=True,
        default="",
        help_text=_(
            "Empty for singular entries. Set for ngettext / msgid_plural "
            "entries; in that case each plural form has its own row "
            "identified by plural_index."
        ),
    )
    plural_index = models.PositiveSmallIntegerField(
        _("Plural Index"),
        default=0,
        help_text=_(
            "0 for singular entries or for the first plural form; 1, 2, ... "
            "for subsequent plural forms defined by the language's plural rule."
        ),
    )
    msgstr = models.TextField(_("Translation"), blank=True, default="")
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Translation")
        verbose_name_plural = _("Translations")
        constraints = [
            models.UniqueConstraint(
                fields=["language_code", "msgid", "plural_index"],
                name="translation_lang_msgid_idx_uniq",
            ),
        ]
        indexes = [
            BTreeIndex(fields=["language_code"], name="translation_lang_ix"),
        ]
        ordering = ["language_code", "msgid", "plural_index"]

    def __str__(self) -> str:
        return f"[{self.language_code}] {self.msgid[:60]}"
