"""Check a document against the storefront's catalogue and this store.

Two kinds of rule meet here. What the storefront owns is the catalogue:
which block types exist, each type's settings schema, where a type may
go, its limits and which settings are translatable or references; Django
checks it with ``jsonschema`` against its copy (``builder.storefront``).
What only Django knows is added on top: the references exist in this
store's schema, a feature-gated block's feature is on for this store, the
store's limits, and rich text goes through the sanitiser policy.

A document is ``{sections: {id: node}, order: [id]}`` and a node is
``{type: "name@version", disabled?, settings, blocks?, blockOrder?}``.
Every problem is an :class:`~builder.errors.Issue` with a JSON pointer
into what the client sent and a stable code; all of them are collected, so
an editor can mark every field at once.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _

from builder import contrast, data_sources, pointers, references
from builder.errors import InvalidCatalogueError, Issue, message_for
from builder.models import DocumentKind, kind_of_key
from builder.storefront import (
    KIND_BLOCK,
    KIND_SECTION,
    BlockDefinition,
    Catalogue,
    Spec,
)
from core.fields.rich_text import validate_rich_text
from core.utils.i18n import available_language_codes
from core.utils.rich_text_images import record_natural_sizes
from core.utils.sanitize import sanitize_html

NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
VERSIONED_TYPE = re.compile(r"^([^@\s]+)@(\d+)$")
NODE_KEYS = frozenset({"type", "disabled", "settings", "blocks", "blockOrder"})
DOCUMENT_KEYS = frozenset({"settings", "sections", "order"})

#: The node id the overlay uses for a document's root settings. Node ids
#: never contain ``@``, so it cannot clash with one.
DOCUMENT_NODE = "@document"

#: The ``groups`` entry a block names to be allowed in the theme document.
THEME_GROUP = DocumentKind.THEME.value


@dataclass(frozen=True, slots=True)
class Validated:
    """A document that passed: what to store, and what it derives."""

    data: dict[str, Any]
    i18n: dict[str, Any]
    block_types: list[str]
    refs: references.Refs
    content_hash: str
    size_bytes: int


def encode(data: Any, i18n: Any) -> bytes:
    """The one canonical serialisation of a document, for its hash and its
    size."""
    return json.dumps(
        [data, i18n],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


@dataclass(slots=True)
class _Walk:
    catalogue: Catalogue
    kind: DocumentKind
    key: str
    feature_on: Callable[[str], bool]
    issues: list[Issue] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    #: node id -> (definition, concrete path of its settings)
    nodes: dict[str, tuple[Spec, pointers.Path]] = field(default_factory=dict)
    block_types: set[str] = field(default_factory=set)
    refs: list[tuple[str, str, str]] = field(default_factory=list)
    #: How many lists the document asks for (``dataLimits.maxQueriesPerDocument``).
    queries: int = 0
    per_type: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: Each feature asked about, so a document asks once per feature.
    features: dict[str, bool] = field(default_factory=dict)

    def add(self, path: str, code: str, message: str) -> None:
        self.issues.append(Issue(path=path, code=code, message=message))


def _is_permutation(order: Any, keys: Any) -> bool:
    return (
        isinstance(order, list)
        and all(isinstance(item, str) for item in order)
        and len(order) == len(set(order))
        and set(order) == set(keys)
    )


def _placement_allowed(
    definition: BlockDefinition, kind: DocumentKind, key: str
) -> bool:
    """Whether a section of this type may sit at the top of the document.

    A block restricted to groups lives only in those groups, and the theme
    is the group ``theme``; a block restricted to templates lives only in
    those (a page is the template ``page``); a block that names neither
    goes in any template or page.
    """
    name = key.partition(":")[2]
    if kind is DocumentKind.THEME:
        return THEME_GROUP in (definition.groups or ())
    if kind is DocumentKind.GROUP:
        return name in (definition.groups or ())
    template = "page" if kind is DocumentKind.PAGE else name
    if definition.templates is not None:
        return template in definition.templates
    return definition.groups is None


def _feature_enabled(walk: _Walk, feature: str) -> bool | None:
    """``None`` when the catalogue names a feature this store has no
    table row for."""
    if feature not in walk.features:
        try:
            walk.features[feature] = walk.feature_on(feature)
        except KeyError:
            return None
    return walk.features[feature]


def _sanitise_rich_text(
    walk: _Walk, definition: Spec, settings_: Any, base: str
) -> None:
    """Hold every rich text setting to the policy: refuse what it would
    lose, store what it leaves (``core.fields.rich_text.RichTextField``)."""
    for pattern in definition.rich_text:
        for path, value in pointers.walk(settings_, pointers.parse(pattern)):
            if not isinstance(value, str):
                continue
            where = f"{base}{pointers.format_path(path)}"
            try:
                validate_rich_text(value)
            except DjangoValidationError as exc:
                for error in exc.error_list:
                    walk.add(where, "unsupported_markup", error.messages[0])
                continue
            pointers.set_at(
                settings_, path, record_natural_sizes(sanitize_html(value))
            )


def _collect_references(
    walk: _Walk,
    definition: Spec,
    settings_: Any,
    base: str,
) -> None:
    for pattern, kind in definition.refs.items():
        for path, value in pointers.walk(settings_, pointers.parse(pattern)):
            if value is None:
                continue
            where = f"{base}{pointers.format_path(path)}"
            if not references.accepts_value(kind, value):
                walk.add(
                    where,
                    "invalid_reference",
                    _("Not a valid %(kind)s reference.") % {"kind": kind},
                )
                continue
            walk.refs.append((kind, str(value), where))
    for pattern in definition.link_refs:
        for path, value in pointers.walk(settings_, pointers.parse(pattern)):
            # ``href`` and ``route`` point at nothing of ours (the
            # catalogue's schema holds them to https or a fixed route).
            if not isinstance(value, dict) or "ref" not in value:
                continue
            where = f"{base}{pointers.format_path(path)}"
            target = value["ref"]
            kind = target.get("kind") if isinstance(target, dict) else None
            ident = target.get("id") if isinstance(target, dict) else None
            if not (
                isinstance(kind, str) and references.accepts_value(kind, ident)
            ):
                walk.add(
                    where,
                    "invalid_reference",
                    _("Not a valid reference."),
                )
                continue
            walk.refs.append((kind, str(ident), where))


def _collect_data(
    walk: _Walk,
    definition: Spec,
    settings_: Any,
    base: str,
) -> None:
    """Hold every typed data prop to its declaration; what a valid one names
    and depends on joins the document's references."""
    limits = walk.catalogue.data_limits
    if limits is None:  # no declaration: ``parse_catalogue`` needs them
        return
    for pattern, declaration in definition.data.items():
        for path, value in pointers.walk(settings_, pointers.parse(pattern)):
            if value is None:
                continue
            where = f"{base}{pointers.format_path(path)}"
            outcome = data_sources.check(declaration, value, limits)
            for problem in outcome.problems:
                walk.add(
                    f"{where}{problem.path}",
                    problem.code,
                    data_sources.message(problem.code),
                )
            walk.refs.extend((k, t, where) for k, t in sorted(outcome.refs))
            walk.refs.extend((k, t, where) for k, t in sorted(outcome.deps))
            if declaration["field"] == "query":
                walk.queries += 1


def _check_node(
    walk: _Walk,
    *,
    node_id: str,
    node: Any,
    path: pointers.Path,
    depth: int,
    parent: BlockDefinition | None,
    levels_allowed: int,
) -> None:
    where = pointers.format_path(path)
    if not NODE_ID.match(node_id):
        walk.add(where, "invalid_node_id", _("Not a valid id."))
    elif node_id in walk.seen_ids:
        walk.add(where, "duplicate_node_id", _("This id is used twice."))
    walk.seen_ids.add(node_id)

    if not isinstance(node, dict):
        walk.add(where, "invalid_document", _("Must be an object."))
        return
    for extra in sorted(set(node) - NODE_KEYS):
        walk.add(f"{where}/{extra}", "invalid_document", _("Unknown property."))
    if "disabled" in node and not isinstance(node["disabled"], bool):
        walk.add(
            f"{where}/disabled", "invalid_document", _("Must be true or false.")
        )
    settings_ = node.get("settings")
    if not isinstance(settings_, dict):
        walk.add(
            f"{where}/settings", "invalid_document", _("Must be an object.")
        )
        settings_ = None

    definition = _definition_of(walk, node, where)
    if definition is None:
        return
    walk.block_types.add(definition.versioned_type)
    walk.per_type[definition.type].append(where)

    _check_placement(walk, definition, where, depth, parent)

    settings_where = f"{where}/settings"
    if settings_ is not None:
        for error in sorted(
            definition.validator.iter_errors(settings_),
            key=lambda e: [str(p) for p in e.absolute_path],
        ):
            walk.add(
                f"{settings_where}"
                f"{pointers.format_path(tuple(error.absolute_path))}",
                "invalid_settings",
                error.message,
            )
        _collect_references(walk, definition, settings_, settings_where)
        _collect_data(walk, definition, settings_, settings_where)
        _sanitise_rich_text(walk, definition, settings_, settings_where)
        walk.nodes[node_id] = (definition, (*path, "settings"))

    _check_children(
        walk,
        node=node,
        definition=definition,
        path=path,
        depth=depth,
        levels_allowed=levels_allowed,
    )


def _definition_of(
    walk: _Walk, node: dict[str, Any], where: str
) -> BlockDefinition | None:
    versioned = node.get("type")
    match = (
        VERSIONED_TYPE.match(versioned) if isinstance(versioned, str) else None
    )
    if match is None:
        walk.add(
            f"{where}/type",
            "invalid_block_type",
            _("The type must look like name@version."),
        )
        return None
    name, version = match.group(1), int(match.group(2))
    definition = walk.catalogue.get(name)
    if definition is None:
        walk.add(
            f"{where}/type",
            "unknown_block_type",
            _("This store has no block called %(type)s.") % {"type": name},
        )
        return None
    if version != definition.version:
        outdated = version < definition.version
        walk.add(
            f"{where}/type",
            "block_version_outdated" if outdated else "unknown_block_version",
            _("%(type)s is at version %(current)s; this is version %(sent)s.")
            % {
                "type": name,
                "current": definition.version,
                "sent": version,
            },
        )
        return None
    return definition


def _check_placement(
    walk: _Walk,
    definition: BlockDefinition,
    where: str,
    depth: int,
    parent: BlockDefinition | None,
) -> None:
    kind = KIND_SECTION if depth == 1 else KIND_BLOCK
    if kind not in definition.kinds:
        walk.add(
            f"{where}/type",
            "not_a_section" if depth == 1 else "not_a_block",
            _("%(type)s cannot be used here.") % {"type": definition.type},
        )
    elif depth == 1 and not _placement_allowed(definition, walk.kind, walk.key):
        walk.add(
            f"{where}/type",
            "block_not_allowed_here",
            _("%(type)s cannot be used in this document.")
            % {"type": definition.type},
        )
    if parent is not None and definition.type not in parent.children_allowed:
        walk.add(
            f"{where}/type",
            "child_not_allowed",
            _("%(type)s cannot go inside %(parent)s.")
            % {"type": definition.type, "parent": parent.type},
        )
    if definition.feature is not None:
        enabled = _feature_enabled(walk, definition.feature)
        if enabled is None:
            walk.add(
                f"{where}/type",
                "unknown_feature",
                _("%(type)s needs a feature this platform does not have.")
                % {"type": definition.type},
            )
        elif not enabled:
            walk.add(
                f"{where}/type",
                "feature_disabled",
                _("%(type)s needs a feature that is off for this store.")
                % {"type": definition.type},
            )


def _check_children(
    walk: _Walk,
    *,
    node: dict[str, Any],
    definition: BlockDefinition,
    path: pointers.Path,
    depth: int,
    levels_allowed: int,
) -> None:
    where = pointers.format_path(path)
    has_blocks, has_order = "blocks" in node, "blockOrder" in node
    if has_blocks != has_order:
        walk.add(
            where,
            "invalid_document",
            _("blocks and blockOrder go together."),
        )
        return
    if not has_blocks:
        return
    blocks, order = node["blocks"], node["blockOrder"]
    if not isinstance(blocks, dict):
        walk.add(f"{where}/blocks", "invalid_document", _("Must be an object."))
        return
    if not _is_permutation(order, blocks):
        walk.add(
            f"{where}/blockOrder",
            "order_mismatch",
            _("blockOrder must list every block exactly once."),
        )
    if not blocks:
        return
    if levels_allowed <= 0:
        walk.add(
            f"{where}/blocks",
            "max_depth_exceeded",
            _("Blocks cannot be nested this deep."),
        )
        return
    limit = min(
        definition.max_blocks or settings.BUILDER_MAX_BLOCKS_PER_NODE,
        settings.BUILDER_MAX_BLOCKS_PER_NODE,
    )
    if len(blocks) > limit:
        walk.add(
            f"{where}/blocks",
            "too_many_blocks",
            _("At most %(limit)s blocks fit here.") % {"limit": limit},
        )
    below = definition.children_max_depth
    for child_id, child in blocks.items():
        _check_node(
            walk,
            node_id=child_id,
            node=child,
            path=(*path, "blocks", child_id),
            depth=depth + 1,
            parent=definition,
            levels_allowed=min(
                levels_allowed - 1, below - 1 if below else levels_allowed
            ),
        )


def _check_shape(data: Any, walk: _Walk) -> bool:
    if not isinstance(data, dict):
        walk.add("", "invalid_document", _("Must be an object."))
        return False
    for extra in sorted(set(data) - DOCUMENT_KEYS):
        walk.add(f"/{extra}", "invalid_document", _("Unknown property."))
    sections, order = data.get("sections"), data.get("order")
    if not isinstance(sections, dict):
        walk.add("/sections", "invalid_document", _("Must be an object."))
        return False
    if not _is_permutation(order, sections):
        walk.add(
            "/order",
            "order_mismatch",
            _("order must list every section exactly once."),
        )
    if len(sections) > settings.BUILDER_MAX_SECTIONS_PER_DOCUMENT:
        walk.add(
            "/sections",
            "too_many_sections",
            _("At most %(limit)s sections fit in a document.")
            % {"limit": settings.BUILDER_MAX_SECTIONS_PER_DOCUMENT},
        )
    return True


def _check_root(walk: _Walk, data: dict[str, Any]) -> None:
    """The document's own settings (a page's SEO fields), against what the
    catalogue declares for this kind of document. They are optional; a
    kind the catalogue gives no root settings takes none.

    A kind that has them can be translated before its default language
    says anything: the editor drops an empty ``settings``, and a store may
    write the English title first. Its overlay is then checked against no
    settings (``_check_node_overlay``) rather than refused as a node the
    document does not have."""
    root = walk.catalogue.roots.get(walk.kind.value)
    if "settings" not in data:
        if root is not None:
            walk.nodes[DOCUMENT_NODE] = (root, ("settings",))
        return
    if root is None:
        walk.add(
            "/settings",
            "document_settings_not_allowed",
            _("This kind of document has no settings of its own."),
        )
        return
    settings_ = data["settings"]
    if not isinstance(settings_, dict):
        walk.add("/settings", "invalid_document", _("Must be an object."))
        return
    for error in sorted(
        root.validator.iter_errors(settings_),
        key=lambda e: [str(p) for p in e.absolute_path],
    ):
        walk.add(
            f"/settings{pointers.format_path(tuple(error.absolute_path))}",
            "invalid_settings",
            error.message,
        )
    _collect_references(walk, root, settings_, "/settings")
    _collect_data(walk, root, settings_, "/settings")
    _sanitise_rich_text(walk, root, settings_, "/settings")
    walk.nodes[DOCUMENT_NODE] = (root, ("settings",))


def _check_per_type_limits(walk: _Walk) -> None:
    for type_name, wheres in walk.per_type.items():
        definition = walk.catalogue.get(type_name)
        limit = definition.max_per_template if definition else None
        if limit is not None and len(wheres) > limit:
            for where in wheres[limit:]:
                walk.add(
                    where,
                    "block_limit_exceeded",
                    _("%(type)s can be used %(limit)s time(s) in a document.")
                    % {"type": type_name, "limit": limit},
                )


def _check_overlay(walk: _Walk, data: dict[str, Any], i18n: Any) -> Any:
    """Validate and sanitise the per-language overlay; returns the overlay
    to store."""
    if not isinstance(i18n, dict):
        walk.add("/i18n", "invalid_i18n", _("Must be an object."))
        return {}
    cleaned = copy.deepcopy(i18n)
    locales = set(available_language_codes())
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE
    for locale, per_node in cleaned.items():
        base = f"/i18n/{locale}"
        if locale not in locales or locale == default:
            walk.add(
                base,
                "unknown_locale",
                _("%(locale)s is not a language that can be translated.")
                % {"locale": locale},
            )
            continue
        if not isinstance(per_node, dict):
            walk.add(base, "invalid_i18n", _("Must be an object."))
            continue
        for node_id, overlay in per_node.items():
            _check_node_overlay(
                walk, data, locale, node_id, overlay, f"{base}/{node_id}"
            )
    return cleaned


def _check_node_overlay(
    walk: _Walk,
    data: dict[str, Any],
    locale: str,
    node_id: str,
    overlay: Any,
    where: str,
) -> None:
    known = walk.nodes.get(node_id)
    if known is None:
        walk.add(where, "unknown_node", _("This document has no such block."))
        return
    if not isinstance(overlay, dict):
        walk.add(where, "invalid_i18n", _("Must be an object."))
        return
    definition, settings_path = known
    # The document's root settings are optional (``_check_root``); a
    # block's always exist.
    base_settings = (
        data.get("settings", {})
        if node_id == DOCUMENT_NODE
        else pointers.get(data, settings_path)
    )
    patterns = [pointers.parse(p) for p in definition.translatable]
    rich = [pointers.parse(p) for p in definition.rich_text]
    merged = copy.deepcopy(base_settings)
    applied: list[tuple[pointers.Path, str]] = []
    for pointer, value in overlay.items():
        field_where = f"{where}{pointer}" if pointer.startswith("/") else where
        try:
            segments = pointers.parse(pointer)
        except ValueError:
            walk.add(where, "invalid_i18n", _("Not a JSON pointer."))
            continue
        if not any(pointers.matches(p, segments) for p in patterns):
            walk.add(
                field_where,
                "untranslatable_setting",
                _("This setting is the same in every language."),
            )
            continue
        concrete = pointers.resolve_concrete(merged, segments)
        if concrete is None:
            walk.add(
                field_where,
                "unknown_setting",
                _("The default language has no such setting."),
            )
            continue
        if any(pointers.matches(p, segments) for p in rich):
            value = _clean_rich_value(walk, value, field_where)
        pointers.set_at(merged, concrete, value)
        overlay[pointer] = value
        applied.append((concrete, field_where))
    # A root translated before the draft has settings stands on its own in
    # that language (``published.resolve_document``), so it answers to the
    # whole schema, as an empty ``settings`` in the draft would.
    whole = node_id == DOCUMENT_NODE and "settings" not in data
    for error in definition.validator.iter_errors(merged):
        error_path = tuple(error.absolute_path)
        for concrete, field_where in applied:
            if error_path[: len(concrete)] == concrete:
                walk.add(field_where, "invalid_settings", error.message)
                break
        else:
            if whole:
                walk.add(where, "invalid_settings", error.message)


def _clean_rich_value(walk: _Walk, value: Any, where: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        validate_rich_text(value)
    except DjangoValidationError as exc:
        for error in exc.error_list:
            walk.add(where, "unsupported_markup", error.messages[0])
        return value
    return record_natural_sizes(sanitize_html(value))


def validate_document(
    *,
    key: str,
    data: Any,
    i18n: Any,
    catalogue: Catalogue,
    feature_on: Callable[[str], bool],
) -> tuple[list[Issue], Validated | None]:
    """Everything wrong with a document, or the document to store.

    References are collected here and checked by
    :func:`check_references` (at publish), so saving a draft whose product
    was deleted meanwhile is still possible.
    """
    kind = kind_of_key(key)
    walk = _Walk(
        catalogue=catalogue,
        kind=kind or DocumentKind.PAGE,
        key=key,
        feature_on=feature_on,
    )
    if kind is None:
        walk.add("", "invalid_key", _("Not a valid document key."))
        return walk.issues, None

    if kind is DocumentKind.TEMPLATE and (
        key.partition(":")[2] not in catalogue.templates
    ):
        walk.add(
            "",
            "unknown_template",
            _("The storefront has no page type called %(name)s.")
            % {"name": key.partition(":")[2]},
        )
        return walk.issues, None

    size = len(encode(data, i18n))
    if size > settings.BUILDER_MAX_DOCUMENT_BYTES:
        walk.add(
            "",
            "document_too_large",
            _("The document is larger than %(limit)s KB.")
            % {"limit": settings.BUILDER_MAX_DOCUMENT_BYTES // 1024},
        )
        return walk.issues, None

    cleaned = copy.deepcopy(data)
    if not _check_shape(cleaned, walk):
        return walk.issues, None
    _check_root(walk, cleaned)

    for section_id, section in cleaned["sections"].items():
        definition_levels = settings.BUILDER_MAX_DEPTH - 1
        _check_node(
            walk,
            node_id=section_id,
            node=section,
            path=("sections", section_id),
            depth=1,
            parent=None,
            levels_allowed=_section_levels(walk, section, definition_levels),
        )
    _check_per_type_limits(walk)
    limits = walk.catalogue.data_limits
    if limits is not None and walk.queries > limits.max_queries_per_document:
        walk.add(
            "", "too_many_queries", data_sources.message("too_many_queries")
        )
    overlay = _check_overlay(walk, cleaned, i18n)

    if walk.issues:
        return walk.issues, None
    payload = encode(cleaned, overlay)
    return [], Validated(
        data=cleaned,
        i18n=overlay,
        block_types=sorted(walk.block_types),
        refs=references.merge(walk.refs),
        content_hash=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _section_levels(walk: _Walk, section: Any, cap: int) -> int:
    """How many levels of blocks a section may hold: the store's cap,
    tightened by the section type's own ``children.maxDepth``."""
    if not isinstance(section, dict):
        return cap
    versioned = section.get("type")
    match = (
        VERSIONED_TYPE.match(versioned) if isinstance(versioned, str) else None
    )
    definition = walk.catalogue.get(match.group(1)) if match else None
    if definition is None or not definition.children_max_depth:
        return cap
    return min(cap, definition.children_max_depth)


def check_before_release(
    key: str, validated: Validated, catalogue: Catalogue
) -> list[Issue]:
    """What stops a RELEASE of a document that is valid to save: a reference
    to something that does not exist, and a theme whose colours miss a
    contrast the catalogue requires (``severity: error`` rules only; a
    warning is the editor's)."""
    issues = check_references(validated.refs)
    kind = kind_of_key(key)
    root = catalogue.roots.get(kind.value) if kind else None
    if root is not None and root.contrast:
        settings_ = validated.data.get("settings") or {}
        try:
            outcomes = contrast.evaluate(root.contrast, settings_)
        except contrast.ContrastCatalogueError as exc:
            raise InvalidCatalogueError() from exc
        issues += [
            Issue(
                path=outcome.rule.path,
                code="insufficient_contrast",
                message=message_for(
                    "insufficient_contrast",
                    params := {
                        "rule": outcome.rule.id,
                        "ratio": contrast.format_ratio(outcome.ratio),
                        "min": f"{outcome.rule.min:g}",
                    },
                    "",
                ),
                params=params,
            )
            for outcome in outcomes
            if outcome.rule.severity == "error" and not outcome.passed
        ]
    return issues


def check_references(refs: references.Refs) -> list[Issue]:
    """One issue per reference to something that does not exist here. The
    pointers a document recorded are already absolute within it."""
    gone = references.missing(refs)
    return [
        Issue(
            path=pointer,
            code="reference_not_found",
            message=message_for(
                "reference_not_found", {"kind": kind, "target": target}, ""
            ),
            params={"kind": kind, "target": target},
        )
        for kind, targets in gone.items()
        for target in sorted(targets)
        for pointer in refs[kind][target]
    ]
