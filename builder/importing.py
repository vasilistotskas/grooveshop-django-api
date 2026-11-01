"""Import a store's legacy page_config layouts and menus as builder
documents, and release them once.

Per store, all or nothing: every layout and menu is converted by the
storefront (``builder.convert``), every result is validated like any other
document (``builder.validation``: catalogue schemas, structure, rich text),
and only when ALL of them pass is anything written, in one transaction:
the documents (a layout that is not published is a draft only), one
version for each published one, and ONE release (number 1). A store that
already has builder data is refused, so the import cannot overwrite what an
editor has done. The legacy tables are not touched: ``page_config`` stays
what the storefront reads until the rendering switch, and is deleted after.

Imported content is released as it is: a block whose feature is off today
still renders nothing when its feature is off, exactly as before, so
feature gates are not applied to the import (they are, on every later save).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import connection, transaction
from django.utils.translation import gettext as _

from builder import legacy, storefront, validation
from builder.convert import ConversionRefused, Converted, convert_legacy
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    VersionKind,
    kind_of_key,
)
from builder.services import _create_release, _new_version

NOTE = "Imported from page layouts"


@dataclass(slots=True)
class StoreReport:
    schema: str
    documents: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)
    #: ``{document key: {"sections": n, "blocks": n}}``; blocks at every depth.
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)
    release: int | None = None
    applied: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


def _problem(
    key: str, code: str, message: str, path: str = ""
) -> dict[str, Any]:
    return {"document": key, "path": path, "code": code, "message": message}


def _convert_all(
    report: StoreReport, templates: frozenset[str]
) -> list[tuple[str, bool, Converted]]:
    converted = []
    for key, published, body in legacy.convert_requests(
        legacy.store_locales(), templates
    ):
        try:
            result = convert_legacy(body)
        except ConversionRefused as refusal:
            report.problems += [
                _problem(
                    key,
                    e.get("code", ""),
                    e.get("message", ""),
                    e.get("path", ""),
                )
                for e in refusal.errors
            ]
            continue
        report.warnings += [{**w, "document": key} for w in result.warnings]
        converted.append((key, published, result))
    return converted


def count_nodes(data: dict[str, Any]) -> dict[str, int]:
    """How many sections a document has and how many blocks inside them, at
    every depth."""

    def blocks(node: dict[str, Any]) -> int:
        children = node.get("blocks") or {}
        return len(children) + sum(blocks(child) for child in children.values())

    sections = data["sections"]
    return {
        "sections": len(sections),
        "blocks": sum(blocks(section) for section in sections.values()),
    }


def import_store(*, dry_run: bool = False) -> StoreReport:
    """Import the store being served. ``dry_run`` goes through everything,
    the writes included, and rolls them back, so what it reports is what
    a real run would do."""
    report = StoreReport(schema=connection.schema_name)
    if (
        BuilderDocument.objects.all_with_deleted().exists()
        or BuilderRelease.objects.exists()
    ):
        report.problems.append(
            _problem(
                "",
                "already_imported",
                _("This store already has page builder data."),
            )
        )
        return report
    catalogue = storefront.fetch_catalogue(connection.schema_name)
    planned = _convert_all(report, catalogue.templates)
    if report.problems:
        return report

    with transaction.atomic():
        stored = _store_documents(report, planned, catalogue)
        if report.problems:
            transaction.set_rollback(True)
            return report
        released = {
            key: validated for key, published, validated in stored if published
        }
        if released:
            documents = {
                d.key: d
                for d in BuilderDocument.objects.filter(key__in=released)
            }
            versions = {
                key: _new_version(
                    None,
                    documents[key],
                    validated,
                    kind=VersionKind.PUBLISH,
                    note=NOTE,
                    catalogue_hash=catalogue.hash,
                ).pk
                for key, validated in released.items()
            }
            report.release = _create_release(None, versions, note=NOTE).number
        if dry_run:
            transaction.set_rollback(True)
        else:
            report.applied = True
    return report


def _store_documents(
    report: StoreReport,
    planned: list[tuple[str, bool, Converted]],
    catalogue: storefront.Catalogue,
) -> list[tuple[str, bool, validation.Validated]]:
    stored = []
    for key, published, converted in planned:
        issues, validated = validation.validate_document(
            key=key,
            data=converted.document,
            i18n=converted.i18n,
            catalogue=catalogue,
            feature_on=lambda name: True,
        )
        if validated is None:
            report.problems += [
                _problem(key, i.code, i.message, i.path) for i in issues
            ]
            continue
        kind = kind_of_key(key)
        assert kind is not None  # legacy.* built the key
        BuilderDocument.objects.create(
            kind=kind,
            key=key,
            draft=validated.data,
            draft_i18n=validated.i18n,
            draft_revision=1,
            draft_catalogue_hash=catalogue.hash,
            published_revision=1 if published else None,
        )
        report.documents.append(key)
        report.counts[key] = count_nodes(validated.data)
        (report.published if published else report.drafts).append(key)
        stored.append((key, published, validated))
    # References last: a menu points at a page document that is created in
    # the same run.
    for key, published, validated in stored:
        if published:
            report.problems += [
                _problem(key, i.code, i.message, i.path)
                for i in validation.check_references(validated.refs)
            ]
    return stored
