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
any document (:func:`lock_store`). Locking documents first would deadlock
with a publish that arrives in between. Library assets come last, in the
order of their ids (``media_services.lock_assets``): whoever edits or
deletes an asset holds its row and takes no other lock after it, so nothing
waits in the other direction. In full: release sequence, documents by key,
assets by id.

What a rewrite writes has to be planned from rows it holds. A command that
reads the store to decide what to write does so after :func:`lock_store`,
in the same transaction: what it read before can have been saved over.

Nothing else keeps what a rewrite replaces, so each row is first copied to a
``BuilderRewriteBackup`` of the run (``BuilderRewriteRun``), in the same
transaction. :func:`restore_run` puts a run back.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Collection, Mapping
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
from builder.services import _lock_release_sequence, published_versions
from tenant.features import STORE_FEATURES

#: A document, its overlay and how a report names it (``template:home version
#: 3``), to copies of both and what moved, by a label.
Transform = Callable[[Any, Any, str], tuple[Any, Any, Counter[str]]]


class DocumentRefused(Exception):
    """A transform cannot bring this document to the new shape. Each argument
    is one reason, reported on a line of its own."""

    #: Whether a person can settle the reasons for the command (a decisions
    #: file), without the document being edited.
    decidable = False


#: How a report marks what belongs to a deleted page.
DELETED_PAGE = "(deleted page)"


def source_of(document: BuilderDocument, version: int | None = None) -> str:
    """Which copy of *document* a report line is about: ``draft`` or
    ``version <number>``, marked when the page is deleted, since nobody can
    open that page to change it."""
    source = "draft" if version is None else f"version {version}"
    return f"{source} {DELETED_PAGE}" if document.is_deleted else source


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
    #: A deleted page's draft that cannot be rewritten, left as it is.
    left: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.drafts or self.versions)


@dataclass(slots=True)
class Leave:
    """The old versions a person said to leave as they are (by the name a
    report gives them, ``template:home version 3``), and which of them a run
    did leave."""

    decided: Collection[str] = frozenset()
    #: ``{version: what was said of it}``, to print.
    used: dict[str, str] = field(default_factory=dict)


#: Marks, in a report, a version that can only be left as it is.
LEAVABLE = "it is in no release, and did not validate before this run either"


def _validates(
    key: str, data: Any, i18n: Any, catalogue: storefront.Catalogue
) -> bool:
    _issues, validated = validation.validate_document(
        key=key,
        data=data,
        i18n=i18n,
        catalogue=catalogue,
        feature_on=known_feature_on,
    )
    return validated is not None


def _block_at(data: Any, path: str) -> str:
    """`` (<type@version>)`` of the node an issue's *path* is in, if any."""
    parts = path.strip("/").split("/")
    node, found = data, ""
    while len(parts) >= 2 and parts[0] in {"sections", "blocks"}:
        holder = node.get(parts[0]) if isinstance(node, dict) else None
        node = holder.get(parts[1]) if isinstance(holder, dict) else None
        if not isinstance(node, dict):
            break
        found = str(node.get("type") or found)
        parts = parts[2:]
    return f" ({found})" if found else ""


def _rewritten(
    *,
    key: str,
    data: Any,
    i18n: Any,
    label: str,
    transform: Transform,
    catalogue: storefront.Catalogue,
    result: Rewritten,
    dead: bool = False,
    leave: Leave | None = None,
    bound: str = "",
) -> validation.Validated | None:
    """The document to store, or ``None`` with nothing to do or with the reason
    it cannot be rewritten added to the problems.

    What is stored is what ``validate_document`` returns against the
    catalogue of TODAY, which is where its references and its hash come
    from. A version was valid when it was made and nothing validates it
    again (``services.publish_version``, ``services.rollback``), so one can
    fail today for a reason this run did not cause: a block the storefront
    has since dropped or changed. Nobody can edit a version, and no decision
    can make it valid. What happens then depends on who reads it:

    * *bound* says a release holds the version, or a publish is waiting to
      release it. Visitors read it, so it has to be moved, and it stops the
      store.
    * *leave* is given for a version in no release, read again only if
      someone rolls back to it. Where it did not validate before the run
      either, it can only be left as it is, which a person has to say
      (``Leave.decided``): until then it stops the store. One that
      validated before the run and does not after it is this run's doing,
      and always stops the store.
    * *dead* is the draft of a deleted page. Nothing reads it again: a page
      that is brought back starts with an empty draft
      (``services._create_document``). It is left as it is and said, and
      stops nothing.

    A reason a person can settle for the command (``DocumentRefused
    .decidable``) stops the store in every case, until it is settled.
    """

    def refuse(reasons: list[str], *, decidable: bool) -> None:
        if dead and not decidable:
            result.left.extend(
                f"{label}: left as it is ({reason})" for reason in reasons
            )
        elif (
            leave is not None
            and not decidable
            and not _validates(key, data, i18n, catalogue)
        ):
            if label in leave.decided:
                why = "; ".join(reasons)
                leave.used[label] = (
                    f"decision: {label} is left as it is ({why})"
                )
            else:
                result.problems.extend(
                    f"{label}: {reason}; {LEAVABLE}" for reason in reasons
                )
        else:
            held = f"; {bound}, so it has to be moved" if bound else ""
            result.problems.extend(
                f"{label}: {reason}{'' if decidable else held}"
                for reason in reasons
            )

    try:
        new_data, new_i18n, moved = transform(data, i18n, label)
    except DocumentRefused as exc:
        refuse([str(reason) for reason in exc.args], decidable=exc.decidable)
        return None
    if not moved:
        return None
    issues, validated = validation.validate_document(
        key=key,
        data=new_data,
        i18n=new_i18n,
        catalogue=catalogue,
        feature_on=known_feature_on,
    )
    if validated is None:
        refuse(
            [
                f"{issue.code} at {issue.path}{_block_at(new_data, issue.path)}"
                for issue in issues
            ],
            decidable=False,
        )
        return None
    result.moved.update(moved)
    return validated


def lock_store() -> None:
    """Take the locks of a rewrite of the current store, for the rest of the
    transaction: its release sequence, then every document, a deleted page
    too, in the order a publish locks them. No draft is saved, no version is
    made or pruned and no release is cut while they are held, so what is read
    after this call is what is there when the rewrite writes. A page created
    meanwhile is the one thing that can still appear."""
    _lock_release_sequence()
    list(
        BuilderDocument.objects.all_with_deleted()
        .select_for_update()
        .order_by("key")
        .values_list("pk", flat=True)
    )


def record_run(run: Run) -> BuilderRewriteRun:
    """The row of *run* in this store, made if it is not there yet."""
    record, _made = BuilderRewriteRun.objects.get_or_create(
        run=run.id,
        defaults={"command": run.command, "decisions": dict(run.decisions)},
    )
    return record


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
    transform: Transform,
    *,
    catalogue: storefront.Catalogue,
    run: Run,
    leave: Leave,
) -> Rewritten:
    """Every draft and version of the current store through *transform*, what
    each held before kept as a backup of *run*. Call it in a transaction: the
    release sequence and then the documents are locked, and the caller rolls
    back on ``problems`` and for a dry run. *leave* names the old versions to
    leave as they are where they cannot be rewritten (:func:`_rewritten`)."""
    lock_store()
    result = Rewritten()
    released = set(published_versions().values())
    waiting = {
        version
        for versions in BuilderScheduledPublish.objects.filter(
            status=ScheduleStatus.PENDING
        ).values_list("versions", flat=True)
        for version in versions.values()
    }

    records: list[BuilderRewriteRun] = []

    def record() -> BuilderRewriteRun:
        """The row of *run*, made with the first row it replaces."""
        if not records:
            records.append(record_run(run))
        return records[0]

    documents = BuilderDocument.objects.all_with_deleted()
    for document in documents.select_for_update():
        validated = _rewritten(
            key=document.key,
            data=document.draft,
            i18n=document.draft_i18n,
            label=f"{document.key} {source_of(document)}",
            transform=transform,
            catalogue=catalogue,
            result=result,
            dead=document.is_deleted,
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
            label=(
                f"{version.document.key} "
                f"{source_of(version.document, version.number)}"
            ),
            transform=transform,
            catalogue=catalogue,
            result=result,
            leave=None if version.pk in released | waiting else leave,
            bound=(
                "it is published"
                if version.pk in released
                else "a publish is waiting to release it"
                if version.pk in waiting
                else ""
            ),
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
                result.left.append(
                    f"{document.key} {source_of(document)}: saved since the run"
                )
        elif version.content_hash == backup.rewritten_hash:
            ready.append((backup, version))
        else:
            result.left.append(
                f"{document.key} {source_of(document, version.number)}: "
                "rewritten since the run"
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
