"""Aggregates that stay correct beside other joins in the outer query."""

from __future__ import annotations

from django.db.models import Count, IntegerField, QuerySet, Subquery
from django.db.models.functions import Coalesce


def subquery_count(queryset: QuerySet, group_by: str) -> Coalesce:
    """``COUNT(*)`` of ``queryset`` for the outer row it is correlated to.

    ``queryset`` filters on an ``OuterRef``; ``group_by`` is the field
    that ties its rows to the outer one. Counting in a correlated
    subquery, rather than ``Count("<relation>")`` on the outer query,
    keeps any other join there from multiplying the result — the fan-out
    that ``Count(..., distinct=True)`` only hides by counting distinct
    values, which is a different number (distinct likers, not likes).
    """
    return Coalesce(
        Subquery(
            queryset.order_by()
            .values(group_by)
            .annotate(n=Count("*"))
            .values("n")[:1],
            output_field=IntegerField(),
        ),
        0,
    )
