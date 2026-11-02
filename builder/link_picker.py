"""What the editor's link pickers ask: find a target, and name a stored one.

A link setting holds ``{ref: {kind, id}}`` (``docs/builder.md``). The editor
needs two things Django alone can answer: the targets that match what the
person typed (``search``), and the name of the target a stored reference
points at, whether or not it can still be linked (``labels``), so a block
shows "Summer sale" and a warning rather than a bare id.

The existence rule is ``references.VISIBLE``, the one the publish check and
the link resolution use: ``search`` answers only what satisfies it and
``labels`` marks each record by it (``active``). Nothing here knows a route:
the storefront builds the path from the slug (``link-targets``).

A label is the record's name in the asked language, else in the default
language, else its slug, in the database (an annotation, not a query per
row), so the order and the limit of a search are by the label shown.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import (
    Case,
    CharField,
    Exists,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce, NullIf, StrIndex, Substr

from builder import references
from builder.models import BuilderDocument, DocumentKind, ReferenceKind

#: The kinds a picker offers, in the order a search without ``kinds``
#: answers them, and the translated field each one is named by. A page
#: has no parler translation (its label is in the document), a content
#: page is named by its slug in the store and its title for the person.
PICKER_KINDS: tuple[ReferenceKind, ...] = (
    ReferenceKind.PRODUCT,
    ReferenceKind.CATEGORY,
    ReferenceKind.BLOG_POST,
    ReferenceKind.BLOG_CATEGORY,
    ReferenceKind.PAGE,
    ReferenceKind.CONTENT_PAGE,
)

_LABEL_FIELD = {
    ReferenceKind.PRODUCT: "name",
    ReferenceKind.CATEGORY: "name",
    ReferenceKind.BLOG_POST: "title",
    ReferenceKind.BLOG_CATEGORY: "name",
    ReferenceKind.CONTENT_PAGE: "title",
}

#: Kinds named by a number; the others (``page``, ``contentPage``) by text.
NUMERIC_KINDS = frozenset(
    {
        ReferenceKind.PRODUCT,
        ReferenceKind.CATEGORY,
        ReferenceKind.BLOG_POST,
        ReferenceKind.BLOG_CATEGORY,
    }
)

#: A page document is named by the text after the ``:`` of its key.
_PAGE_SEO_TITLE = "/seoTitle"
_DOCUMENT_NODE = "@document"


def _text(expression: Any) -> NullIf:
    return NullIf(expression, Value(""), output_field=CharField())


def _labelled(
    queryset: QuerySet[Any], kind: ReferenceKind, locale: str
) -> QuerySet[Any]:
    """``queryset`` with ``label``: the name in ``locale``, else in the
    default language, else the slug."""
    translation = queryset.model._parler_meta.root_model
    field = _LABEL_FIELD[kind]

    def named(code: str) -> NullIf:
        return _text(
            Subquery(
                translation.objects.filter(
                    master=OuterRef("pk"), language_code=code
                ).values(field)[:1]
            )
        )

    return queryset.annotate(
        label=Coalesce(
            named(locale),
            named(settings.PARLER_DEFAULT_LANGUAGE_CODE),
            "slug",
            output_field=CharField(),
        )
    )


def _page_documents(locale: str) -> QuerySet[BuilderDocument]:
    """The page and template documents with ``name`` (the key after the
    ``:``) and ``label``: a store page's SEO title in ``locale``, else its
    default one, else its name; a fixed page type is named by its key (the
    editor labels it with its own wording)."""
    name = Substr("key", StrIndex("key", Value(":")) + 1)
    seo_title = Coalesce(
        _text(KT(f"draft_i18n__{locale}__{_DOCUMENT_NODE}__{_PAGE_SEO_TITLE}")),
        _text(KT("draft__settings__seoTitle")),
        output_field=CharField(),
    )
    return references.VISIBLE[ReferenceKind.PAGE]().annotate(
        name=name,
        label=Case(
            When(kind=DocumentKind.TEMPLATE, then=name),
            default=Coalesce(seo_title, name, output_field=CharField()),
            output_field=CharField(),
        ),
    )


def _matching(queryset: QuerySet[Any], kind: str, query: str) -> QuerySet[Any]:
    match = Q(label__icontains=query)
    if kind == ReferenceKind.PAGE:
        match |= Q(name__icontains=query)
    else:
        match |= Q(slug__icontains=query)
        if (
            kind in NUMERIC_KINDS
            and query.isascii()
            and query.isdigit()
            and int(query) <= references.MAX_ID
        ):
            match |= Q(pk=int(query))
    return queryset.filter(match)


def _result(
    kind: str, key: Any, label: str | None, slug: str, *, active: bool
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": key,
        "label": label,
        "slug": slug,
        "active": active,
    }


def search(
    query: str, kinds: list[ReferenceKind], locale: str
) -> list[dict[str, Any]]:
    """The targets whose name or slug contains ``query``, per kind in the
    order of ``kinds`` and by label, at most
    ``BUILDER_LINK_SEARCH_LIMIT`` of each. Only what a link may point at
    (``references.VISIBLE``); one query per kind. A query shorter than
    ``BUILDER_LINK_SEARCH_MIN_CHARS`` finds nothing."""
    query = query.strip()
    if len(query) < settings.BUILDER_LINK_SEARCH_MIN_CHARS:
        return []
    limit = settings.BUILDER_LINK_SEARCH_LIMIT
    results: list[dict[str, Any]] = []
    for kind in kinds:
        if kind == ReferenceKind.PAGE:
            pages = _matching(_page_documents(locale), kind, query)
            results.extend(
                {
                    **_result(kind, name, label, name, active=True),
                    "source": document_kind,
                }
                for name, label, document_kind in pages.order_by(
                    "label", "key"
                ).values_list("name", "label", "kind")[:limit]
            )
            continue
        rows = _matching(
            _labelled(references.VISIBLE[kind](), kind, locale), kind, query
        ).order_by("label", "pk")
        results.extend(
            _result(
                kind,
                slug if kind == ReferenceKind.CONTENT_PAGE else pk,
                label,
                slug,
                active=True,
            )
            for pk, slug, label in rows.values_list("pk", "slug", "label")[
                :limit
            ]
        )
    return results


def labels(
    wanted: dict[ReferenceKind, set[Any]], locale: str
) -> dict[str, dict[str, dict[str, Any]]]:
    """``{kind: {id: {label, active}}}`` for the ids asked about, whether
    or not they can still be linked: ``active`` is the existence rule, and
    an id that exists nowhere is ``{label: None, active: False}``. A page
    id also carries ``source`` (``page`` wins over ``template``, as in the
    public read). One query per kind."""
    answer: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, ids in wanted.items():
        found = (
            _page_labels(ids, locale)
            if kind == ReferenceKind.PAGE
            else _record_labels(kind, ids, locale)
        )
        answer[kind.value] = {
            str(key): found.get(str(key), {"label": None, "active": False})
            for key in ids
        }
    return answer


def _record_labels(
    kind: ReferenceKind, ids: set[Any], locale: str
) -> dict[str, dict[str, Any]]:
    visible = references.VISIBLE[kind]()
    by_slug = kind == ReferenceKind.CONTENT_PAGE
    key = "slug" if by_slug else "pk"
    rows = (
        _labelled(visible.model.objects.all(), kind, locale)
        .filter(**{f"{key}__in": ids})
        .annotate(
            linkable=Exists(visible.filter(pk=OuterRef("pk"))),
        )
    )
    return {
        str(value): {"label": label, "active": active}
        for value, label, active in rows.values_list(key, "label", "linkable")
    }


def _page_labels(ids: set[str], locale: str) -> dict[str, dict[str, Any]]:
    documents = (
        _page_documents(locale)
        .filter(
            key__in={
                f"{kind}:{name}"
                for name in ids
                for kind in (DocumentKind.PAGE, DocumentKind.TEMPLATE)
            }
        )
        .order_by("kind")  # "page" sorts before "template"
    )
    found: dict[str, dict[str, Any]] = {}
    for name, label, kind in documents.values_list("name", "label", "kind"):
        found.setdefault(name, {"label": label, "active": True, "source": kind})
    return found
