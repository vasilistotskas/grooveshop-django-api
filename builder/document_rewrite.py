"""Rewrite every stored draft and version of a store, in place.

``BuilderVersion`` is otherwise immutable, and only the management commands
that move stored documents to a new shape come through here
(``persist_block_upgrades``, ``migrate_builder_media``). Every document is
rewritten, a deleted page's too: its versions are still stored. A version
keeps its number, since what it renders is meant to stay the same.

A draft moves to a new revision, because its content changed under whoever
has it open: an editor that loaded the old revision is answered the usual
revision conflict on its next save, and cannot write the old shape back.
Whether the store has unpublished changes does not change with it: a draft
that was at its published revision stays there, and a publish waiting for
its time follows the draft it froze (:func:`_move_revision`).

Each rewritten document goes through ``validate_document`` against the store's
catalogue, and what that returns is what is stored, hash and references
included. A document that cannot be rewritten is a problem to report, and the
caller, who holds the transaction, rolls back on any. A dry run is the same
call in a transaction the caller rolls back, so it takes every step the real
run does.

**Lock order.** Every writer of a release takes the store's release sequence
(``services._lock_release_sequence``) and then its documents: ``publish``,
``rollback``, ``delete_page``, a scheduled publish. A rewrite and a restore
end with a release, so they take the release sequence first as well, before
any document. Locking documents first would deadlock with a publish that
arrives in between.

Nothing else keeps what a rewrite replaces, so each row is first copied to a
``BuilderRewriteBackup`` of the run (``BuilderRewriteRun``), in the same
transaction. :func:`restore_run` puts a run back.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from builder import storefront, validation
from builder.models import (
    BuilderDocument,
    BuilderRewriteBackup,
    BuilderRewriteRun,
    BuilderScheduledPublish,
    BuilderVersion,
    ScheduleStatus,
)
from builder.services import _lock_release_sequence
from tenant.features import STORE_FEATURES

#: A document, its overlay and how a report names it (``template:home version
#: 3``), to copies of both and what moved, by a label.
Transform = Callable[[Any, Any, str], tuple[Any, Any, Counter[str]]]


class DocumentRefused(Exception):
    """A transform cannot bring this document to the new shape. Each argument
    is one reason, reported on a line of its own."""


def known_feature_on(name: str) -> bool:
    """What the validation asks of a feature during a rewrite. A store may
    switch a feature off with its block still on a page, which is for its next
    publish to refuse and no reason for that page to stay in the old shape; a
    name the platform does not have raises the ``KeyError`` it reports as
    ``unknown_feature``."""
    return STORE_FEATURES[name] is not None


@dataclass(frozen=True, slots=True)
class Run:
    """What a rewrite is recorded under: the id of the invocation, which is
    the same in every store it reaches, the command, and what a person
    decided for this store."""

    id: UUID
    command: str
    decisions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Rewritten:
    """What a rewrite did."""

    drafts: int = 0
    versions: int = 0
    moved: Counter[str] = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.drafts or self.versions)


def _rewritten(
    *,
    key: str,
    data: Any,
    i18n: Any,
    label: str,
    transform: Transform,
    catalogue: storefront.Catalogue,
    result: Rewritten,
) -> validation.Validated | None:
    """The document to store, or ``None`` with nothing to do or with the reason
    it cannot be rewritten added to the problems."""
    try:
        new_data, new_i18n, moved = transform(data, i18n, label)
    except DocumentRefused as exc:
        result.problems.extend(f"{label}: {reason}" for reason in exc.args)
        return None
    if not moved:
        return None
    result.moved.update(moved)
    issues, validated = validation.validate_document(
        key=key,
        data=new_data,
        i18n=new_i18n,
        catalogue=catalogue,
        feature_on=known_feature_on,
    )
    if validated is None:
        result.problems.extend(
            f"{label}: {issue.code} at {issue.path}" for issue in issues
        )
    return validated


def _move_revision(document: BuilderDocument) -> None:
    """The draft of *document* was replaced by something no editor saved, so
    it is a new revision: a save that names the old one is refused.

    The rest of what a revision means is kept as it was. A draft at its
    published revision stays published, because the version it matches was
    rewritten the same way. A publish that waits for its time and froze this
    revision froze this content, so it names the new revision, and marks the
    draft published when it runs as it would have.
    """
    old, new = document.draft_revision, document.draft_revision + 1
    moved = {"draft_revision": new}
    if document.published_revision == old:
        moved["published_revision"] = new
    BuilderDocument._base_manager.filter(pk=document.pk).update(**moved)
    waiting = BuilderScheduledPublish.objects.select_for_update().filter(
        status=ScheduleStatus.PENDING,
        revisions__contains={document.key: old},
    )
    for schedule in waiting:
        BuilderScheduledPublish.objects.filter(pk=schedule.pk).update(
            revisions={**schedule.revisions, document.key: new}
        )


def _replace(
    row: BuilderDocument | BuilderVersion,
    columns: dict[str, Any],
    *,
    record: BuilderRewriteRun,
    rewritten_hash: str,
) -> None:
    """Store *columns* on *row*, what they replace kept under *record*."""
    is_version = isinstance(row, BuilderVersion)
    BuilderRewriteBackup.objects.create(
        run=record,
        document=row.document if is_version else row,
        version=row if is_version else None,
        original={name: getattr(row, name) for name in columns},
        rewritten_hash=rewritten_hash,
    )
    type(row)._base_manager.filter(pk=row.pk).update(**columns)


def rewrite_store(
    transform: Transform, *, catalogue: storefront.Catalogue, run: Run
) -> Rewritten:
    """Every draft and version of the current store through *transform*, what
    each held before kept as a backup of *run*. Call it in a transaction: the
    release sequence and then the documents are locked, and the caller rolls
    back on ``problems`` and for a dry run."""
    _lock_release_sequence()
    result = Rewritten()
    records: list[BuilderRewriteRun] = []

    def record() -> BuilderRewriteRun:
        """The row of *run*, made with the first row it replaces."""
        if not records:
            records.append(
                BuilderRewriteRun.objects.create(
                    run=run.id,
                    command=run.command,
                    decisions=dict(run.decisions),
                )
            )
        return records[0]

    documents = BuilderDocument.objects.all_with_deleted()
    for document in documents.select_for_update():
        validated = _rewritten(
            key=document.key,
            data=document.draft,
            i18n=document.draft_i18n,
            label=f"{document.key} draft",
            transform=transform,
            catalogue=catalogue,
            result=result,
        )
        if validated is None:
            continue
        result.drafts += 1
        _replace(
            document,
            {
                "draft": validated.data,
                "draft_i18n": validated.i18n,
                "draft_catalogue_hash": catalogue.hash,
            },
            record=record(),
            rewritten_hash=validated.content_hash,
        )
        _move_revision(document)
    for version in BuilderVersion.objects.select_related("document").iterator():
        validated = _rewritten(
            key=version.document.key,
            data=version.data,
            i18n=version.i18n,
            label=f"{version.document.key} version {version.number}",
            transform=transform,
            catalogue=catalogue,
            result=result,
        )
        if validated is None:
            continue
        result.versions += 1
        _replace(
            version,
            {
                "data": validated.data,
                "i18n": validated.i18n,
                "block_types": validated.block_types,
                "refs": validated.refs,
                "catalogue_hash": catalogue.hash,
                "content_hash": validated.content_hash,
                "size_bytes": validated.size_bytes,
            },
            record=record(),
            rewritten_hash=validated.content_hash,
        )
    return result


@dataclass(slots=True)
class Restored:
    """What putting a run back did."""

    drafts: int = 0
    versions: int = 0
    #: A row that no longer holds what the run stored, and so was not put
    #: back, with its backup kept.
    left: list[str] = field(default_factory=list)
    #: Backups whose document or version no longer exists, removed.
    gone: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.drafts or self.versions)


def _draft_hash(document: BuilderDocument) -> str:
    return hashlib.sha256(
        validation.encode(document.draft, document.draft_i18n)
    ).hexdigest()


def restore_run(run: UUID, *, leave_changed: bool) -> Restored:
    """Put back what *run* replaced in the current store, and delete the
    backups it used. Call it in a transaction: the release sequence and then
    the documents are locked.

    Only a row that still holds what the run stored can be put back: a draft
    saved since is someone's work, and a version rewritten since belongs to
    that later run, which is the one to restore first. One such row and
    nothing of the store is restored, so a store is never left half in each
    shape by accident: the rows are answered in ``left``. With
    *leave_changed* the rest is put back all the same, and those rows stay
    as they are, their backups kept.

    A restored draft moves to a new revision like a rewritten one. A backup
    whose document or version no longer exists has nothing to restore, and
    goes.
    """
    _lock_release_sequence()
    result = Restored()
    record = BuilderRewriteRun.objects.filter(run=run).first()
    if record is None:
        return result
    backups = list(
        BuilderRewriteBackup.objects.select_for_update().filter(run=record)
    )
    documents = {
        document.pk: document
        for document in BuilderDocument.objects.all_with_deleted()
        .select_for_update()
        .filter(pk__in={backup.document_id for backup in backups})
    }
    versions = BuilderVersion.objects.in_bulk(
        {
            backup.version_id
            for backup in backups
            if backup.version_id is not None
        }
    )
    ready: list[tuple[BuilderRewriteBackup, BuilderDocument | BuilderVersion]]
    ready, gone = [], []
    for backup in backups:
        document = documents.get(backup.document_id)
        version = versions.get(backup.version_id)
        if document is None or (backup.version_id and version is None):
            gone.append(backup.pk)
        elif version is None:
            if _draft_hash(document) == backup.rewritten_hash:
                ready.append((backup, document))
            else:
                result.left.append(f"{document.key} draft: saved since the run")
        elif version.content_hash == backup.rewritten_hash:
            ready.append((backup, version))
        else:
            result.left.append(
                f"{document.key} version {version.number}: rewritten since "
                "the run"
            )
    if result.left and not leave_changed:
        return result
    for backup, row in ready:
        type(row)._base_manager.filter(pk=row.pk).update(**backup.original)
        if isinstance(row, BuilderDocument):
            _move_revision(row)
            result.drafts += 1
        else:
            result.versions += 1
    BuilderRewriteBackup.objects.filter(
        pk__in=[*gone, *(backup.pk for backup, _row in ready)]
    ).delete()
    result.gone = len(gone)
    if not record.backups.exists():
        record.delete()
    return result
