"""Rewrite every stored draft and version of a store, in place.

``BuilderVersion`` is otherwise immutable, and only the management commands
that move stored documents to a new shape come through here
(``persist_block_upgrades``, ``migrate_builder_media``). A version keeps its
number and a draft keeps its revision, so a store is not told it has unpublished
changes: what the document renders is meant to stay the same.

Each rewritten document goes through ``validate_document`` against the store's
catalogue, and what that returns is what is stored, hash and references
included. A document that cannot be rewritten is a problem to report, and the
caller, who holds the transaction, rolls back on any.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from builder import storefront, validation
from builder.models import BuilderDocument, BuilderVersion
from tenant.features import STORE_FEATURES

#: A document and its overlay, to copies of both and what moved, by a label.
Transform = Callable[[Any, Any], tuple[Any, Any, Counter[str]]]


class DocumentRefused(Exception):
    """A transform cannot bring this document to the new shape."""


def known_feature_on(name: str) -> bool:
    """What the validation asks of a feature during a rewrite. A store may
    switch a feature off with its block still on a page, which is for its next
    publish to refuse and no reason for that page to stay in the old shape; a
    name the platform does not have raises the ``KeyError`` it reports as
    ``unknown_feature``."""
    return STORE_FEATURES[name] is not None


@dataclass(slots=True)
class Rewritten:
    """What a rewrite did, or in a dry run would do."""

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
        new_data, new_i18n, moved = transform(data, i18n)
    except DocumentRefused as exc:
        result.problems.append(f"{label}: {exc}")
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


def rewrite_store(
    transform: Transform,
    *,
    catalogue: storefront.Catalogue,
    dry_run: bool,
) -> Rewritten:
    """Every draft and version of the current store through *transform*. Call
    it in a transaction: the documents are locked, and the caller rolls back on
    ``problems``. A dry run does all of it and writes nothing."""
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
        if not dry_run:
            BuilderDocument.objects.filter(pk=document.pk).update(
                draft=validated.data,
                draft_i18n=validated.i18n,
                draft_catalogue_hash=catalogue.hash,
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
        if not dry_run:
            BuilderVersion.objects.filter(pk=version.pk).update(
                data=validated.data,
                i18n=validated.i18n,
                block_types=validated.block_types,
                refs=validated.refs,
                catalogue_hash=catalogue.hash,
                content_hash=validated.content_hash,
                size_bytes=validated.size_bytes,
            )
    return result
