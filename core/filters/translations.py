"""Predicates for filtering on django-parler translated fields.

``translations__<field>`` is a reverse FK to the translation table, which
holds ONE ROW PER LANGUAGE. Filtering or annotating through it multiplies
the outer row once per matching translation: with el/en/de active,
``GET /api/v1/blog/comment?minContentLength=10`` returned a single
comment three times (measured: ``[22, 22, 22]``) and the paginated
``count`` was inflated to match.

``distinct=True`` on the filter is django-filter's documented answer for
a filter that spans a relationship, and it is enough while the predicate
only adds a ``WHERE`` clause. It is NOT enough once the predicate needs
an annotation: ``Length("translations__content")`` lands in the
``SELECT`` list with a different value per language, so ``DISTINCT``
sees three distinct rows and keeps all three.

``any_translation`` sidesteps both problems by asking the question as a
correlated ``EXISTS`` over the translation model: one outer row whatever
the lookup, no ``DISTINCT`` sort, and the annotation stays inside the
subquery where it belongs.

It also makes the negative case expressible. ``translations__content=""``
means "SOME language is empty", so a comment translated in Greek but not
in German satisfied both sides of a has-content filter at once. The
honest negation is "no translation matches", which is ``~`` on the
``Exists`` — not the same predicate with inverted lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Exists, OuterRef, Q

if TYPE_CHECKING:
    from django.db.models import Model

__all__ = ["any_translation"]


def any_translation(
    model: type[Model],
    field: str,
    *conditions: Q,
    annotate: dict | None = None,
    **lookups,
) -> Exists:
    """``Exists`` over ``model``'s translations of the outer row.

    ``model`` is the TRANSLATABLE model (``BlogComment``), not its
    translation model: which field lives in which translation table is
    parler's business, so ``field`` — the translated field the predicate
    is about — resolves the table through ``_parler_meta``.

    ``conditions`` and ``lookups`` are ANDed and applied to the
    translation rows, exactly as in ``QuerySet.filter``. ``annotate`` is
    applied first, so an expression over a translated column
    (``{"length": Length("content")}``) can be compared without joining
    the translation table into the outer query.

    Negate it with ``~`` for "no translation matches"::

        queryset.filter(~any_translation(model, "content", content__gt=""))
    """
    translations = model._parler_meta.get_model_by_field(field)
    subquery = translations.objects.filter(master=OuterRef("pk"))
    if annotate:
        subquery = subquery.annotate(**annotate)
    return Exists(subquery.filter(*conditions, **lookups))
