"""A filter mixin that is not a FilterSet contributes nothing, silently.

django-filter collects declared filters in
`FilterSetMetaclass.get_declared_filters`, which reads the class's own
`attrs` and then any base that already carries a `declared_filters`
dict. A plain-class mixin has neither, so its `Filter` attributes are
dropped with no error and no warning — the query parameter simply never
exists, and the endpoint answers 200 with the whole unfiltered list.

Every mixin in `core/filters/core.py` was a plain class. `schema.yml`
carried zero occurrences of the affected parameter names, and two
staff-gated filters read as authorization while doing nothing at all.
The camel-case FilterSets appeared to work only because they restated
the same filters verbatim in their own class bodies.
"""

from __future__ import annotations

import inspect

import django_filters
import pytest
from django_filters import rest_framework as filters

from core.filters import core as core_filters


def _mixin_classes():
    return [
        obj
        for _, obj in inspect.getmembers(core_filters, inspect.isclass)
        if obj.__module__ == core_filters.__name__
    ]


@pytest.mark.parametrize("mixin", _mixin_classes(), ids=lambda c: c.__name__)
def test_every_mixin_is_a_filterset(mixin):
    assert issubclass(mixin, filters.FilterSet), (
        f"{mixin.__name__} is a plain class, so django-filter will drop "
        f"every Filter it declares. Subclass FilterSet."
    )


@pytest.mark.parametrize("mixin", _mixin_classes(), ids=lambda c: c.__name__)
def test_every_mixin_actually_contributes_its_filters(mixin):
    """The property the FilterSet base is there to produce."""
    declared_on_the_class = {
        name
        for name, value in vars(mixin).items()
        if isinstance(value, django_filters.Filter)
    }
    collected = set(mixin.declared_filters)

    missing = declared_on_the_class - collected
    assert not missing, (
        f"{mixin.__name__} declares {sorted(missing)} but django-filter "
        f"collected {sorted(collected)}"
    )
    assert collected, f"{mixin.__name__} contributes no filters at all"


def test_a_consumer_inherits_them():
    """End to end: the one FilterSet that composes the metadata mixin."""
    from product.filters.product import ProductFilter

    exposed = set(ProductFilter.base_filters)

    assert {
        "uuid",
        "metadata_has_key",
        "metadata_has_keys",
        "metadata_has_any_keys",
        "metadata_contains",
        "private_metadata_has_key",
    } <= exposed
