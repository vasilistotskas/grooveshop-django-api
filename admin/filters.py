"""List filters shared across admins.

Unfold's ``RangeNumericListFilter`` already turns ``?<parameter>_from=``
and ``?<parameter>_to=`` into ``<parameter>__gte``/``__lte`` on the
queryset, so a range over a value every row carries (a field, or an
annotation the admin's ``get_queryset`` always adds) needs nothing but a
``title`` and a ``parameter_name``. ``AnnotatedRangeFilter`` is for a
value that must be annotated first.
"""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeNumericListFilter


class AnnotatedRangeFilter(RangeNumericListFilter):
    """A from/to range over a value annotated only when the filter is used.

    Django runs every list filter's ``queryset()`` on every changelist
    load. Annotating unconditionally put a JOIN + GROUP BY on the main
    fetch of every page: the blog-post changelist went from ~80 to over
    1000 queries, and the product one spent ~2s on it. ``annotate()``
    must add an annotation named ``parameter_name``.
    """

    def annotate(self, queryset: QuerySet) -> QuerySet:
        raise NotImplementedError

    def queryset(self, request, queryset):
        bounds = (
            self.used_parameters.get(f"{self.parameter_name}_{end}")
            for end in ("from", "to")
        )
        if all(value in (None, "") for value in bounds):
            return queryset
        return super().queryset(request, self.annotate(queryset))


class LikesCountFilter(AnnotatedRangeFilter):
    """For querysets with ``with_likes_count()`` (products, blog posts)."""

    title = _("Likes")
    parameter_name = "likes_count"

    def annotate(self, queryset):
        return queryset.with_likes_count()
