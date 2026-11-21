"""Every ordering key the schema advertises is the field it names.

The OpenAPI extension advertises ``snake_to_camel(field)`` for each
ordering field and the filter reads the key back with ``camel_to_snake``.
The two must be inverse on every field a route sorts by. They were not: a
hand-written ``camel_to_snake`` read the advertised ``alpha2`` as
``alpha2``, never ``alpha_2``, so ``?ordering=alpha2`` on the countries was
dropped in silence and the default order served.
"""

from __future__ import annotations

from django.urls import URLPattern, URLResolver, get_resolver

from core.filters.camel_case_ordering import CamelCaseOrderingFilter
from core.utils.string_case import snake_to_camel


def _ordering_fields() -> set[str]:
    fields: set[str] = set()

    def walk(patterns):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns)
                continue
            assert isinstance(pattern, URLPattern)
            view = getattr(pattern.callback, "cls", None)
            if view is None:
                continue
            fields.update(getattr(view, "ordering_fields", None) or ())
            for declared in getattr(view, "action_ordering", {}).values():
                fields.update(declared.fields)

    walk(get_resolver().url_patterns)
    return fields


def test_every_advertised_ordering_key_reads_back_as_its_field():
    fields = _ordering_fields()
    assert "alpha_2" in fields  # the control: a digit after an underscore

    misread = {
        field: CamelCaseOrderingFilter.camel_to_snake(snake_to_camel(field))
        for field in fields
        if CamelCaseOrderingFilter.camel_to_snake(snake_to_camel(field))
        != field
    }

    assert not misread, misread
