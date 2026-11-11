"""Rewrite every stored draft and version of a store, in place.

``BuilderVersion`` is otherwise immutable, and only the management commands
that move stored documents to a new shape come through here
(``persist_block_upgrades``, ``migrate_builder_media``). A version keeps its
number and a draft keeps its revision, so a store is not told it has unpublished
changes: what the document renders is meant to stay the same.

Each rewritten document goes through ``validate_document`` against the store's
catalogue, and what that returns is what is stored, hash and references
included. A document that cannot be rewritten is a problem to report, and the
caller, who holds the transaction, rolls back on any. A dry run is the same
call in a transaction the caller rolls back, so it takes every step the real
run does.

Nothing else keeps what a rewrite replaces, so each row is first copied to a
``BuilderRewriteBackup`` under the id of the run, in the same transaction.
:func:`restore_run` puts a run back: every row that still holds what the run
stored gets its columns as they were, and its backup goes. A row changed since
(a draft someone saved, a version another rewrite moved on) is left as it is
and named, with its backup kept.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from builder import storefront, validation
from builder.models import (
    BuilderDocument,
    BuilderRewriteBackup,
    BuilderVersion,
)
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


def _replace(
    row: BuilderDocument | BuilderVersion,
    columns: dict[str, Any],
    *,
    run: UUID,
    rewritten_hash: str,
) -> None:
    """Store *columns* on *row*, what they replace kept under *run*."""
    is_version = isinstance(row, BuilderVersion)
    BuilderRewriteBackup.objects.create(
        run=run,
        document=row.document if is_version else row,
        version=row if is_version else None,
        original={name: getattr(row, name) for name in columns},
        rewritten_hash=rewritten_hash,
    )
    type(row)._base_manager.filter(pk=row.pk).update(**columns)


def rewrite_store(
    transform: Transform, *, catalogue: storefront.Catalogue, run: UUID
) -> Rewritten:
    """Every draft and version of the current store through *transform*, what
    each held before kept as a backup of *run*. Call it in a transaction: the
    documents are locked, and the caller rolls back on ``problems`` and for a
    dry run."""
    result = Rewritten()
    for document in BuilderDocument.objects.select_for_update():
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
            run=run,
            rewritten_hash=validated.content_hash,
        )
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
            run=run,
            rewritten_hash=validated.content_hash,
        )
    return result


@dataclass(slots=True)
class Restored:
    """What putting a run back did."""

    drafts: int = 0
    versions: int = 0
    #: A row that no longer holds what the run stored, with its backup kept.
    left: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.drafts or self.versions)


def _draft_hash(document: BuilderDocument) -> str:
    return hashlib.sha256(
        validation.encode(document.draft, document.draft_i18n)
    ).hexdigest()


def restore_run(run: UUID) -> Restored:
    """Put back what *run* replaced in the current store, and delete the
    backups it used. Call it in a transaction. Only a row that still holds
    what the run stored is put back: a draft saved since is someone's work,
    and a version rewritten since belongs to that later run, which is the one
    to restore first."""
    result = Restored()
    backups = BuilderRewriteBackup.objects.filter(run=run).select_related(
        "document", "version"
    )
    for backup in backups.select_for_update(of=("self", "document")):
        version = backup.version
        if version is None:
            row, label = backup.document, f"{backup.document.key} draft"
            held = _draft_hash(backup.document)
        else:
            row = version
            label = f"{backup.document.key} version {version.number}"
            held = version.content_hash
        if held != backup.rewritten_hash:
            result.left.append(f"{label}: changed since the run, left as it is")
            continue
        type(row)._base_manager.filter(pk=row.pk).update(**backup.original)
        backup.delete()
        if version is None:
            result.drafts += 1
        else:
            result.versions += 1
    return result
