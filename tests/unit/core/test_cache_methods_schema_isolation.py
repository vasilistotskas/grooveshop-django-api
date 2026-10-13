"""`cache_methods` must not make viewsets share their schema annotations.

drf-spectacular keeps a view method's ``@extend_schema`` in the method's
``kwargs["schema"]``. ``cache_methods`` replaces ``list``/``retrieve`` with
a ``method_decorator`` wrapper, and ``functools.update_wrapper`` copies the
wrapped function's ``__dict__`` shallowly, so every wrapper held the SAME
``kwargs`` dict as ``BaseModelViewSet.list`` (which ``BaseModelViewSet``'s
own ``@extend_schema_view`` gives one). drf-spectacular isolates an
inherited method before annotating it (``isolate_view_method``), but skips
that for a method defined on the class itself, which the wrapper is. So
each cached viewset's ``@extend_schema_view`` wrote into one shared dict
and the last viewset decorated won for all of them.

It only showed where the response cache is on (the decorator is a no-op
under ``DISABLE_CACHE`` and under pytest): the schema served in production
and the one CI generates documented ``GET /api/v1/country`` as returning
PayWay rows, and lost 19 components. These tests force the decorator on.
"""

from __future__ import annotations

import sys

import pytest
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers

from core.api.views import BaseModelViewSet
from core.utils.views import cache_methods


class _FirstSerializer(serializers.Serializer):
    first = serializers.CharField()


class _SecondSerializer(serializers.Serializer):
    second = serializers.CharField()


@pytest.fixture
def caching_on(monkeypatch, settings):
    """``cache_methods`` decides at decoration time; make it decorate."""
    settings.DISABLE_CACHE = False
    monkeypatch.setattr(sys, "argv", ["manage.py", "spectacular"])


def _cached_viewset(name, serializer):
    """A viewset decorated the way the real ones are: ``cache_methods``
    first (innermost), ``extend_schema_view`` over it."""
    cls = type(name, (BaseModelViewSet,), {"__module__": __name__})
    cls = cache_methods(60, methods=["list", "retrieve"])(cls)
    return extend_schema_view(
        list=extend_schema(responses=serializer),
        retrieve=extend_schema(responses=serializer),
    )(cls)


def _schema_of(method):
    return method.kwargs["schema"]


@pytest.mark.usefixtures("caching_on")
@pytest.mark.parametrize("method_name", ["list", "retrieve"])
def test_each_cached_viewset_keeps_its_own_schema(method_name):
    base_schema = _schema_of(getattr(BaseModelViewSet, method_name))

    first = _cached_viewset("FirstViewSet", _FirstSerializer)
    second = _cached_viewset("SecondViewSet", _SecondSerializer)

    first_method = getattr(first, method_name)
    second_method = getattr(second, method_name)
    assert first_method.kwargs is not second_method.kwargs
    assert _schema_of(first_method) is not _schema_of(second_method)
    # Neither viewset's annotation leaked into the base it derives from.
    assert _schema_of(getattr(BaseModelViewSet, method_name)) is base_schema


@pytest.mark.usefixtures("caching_on")
def test_the_cached_method_still_carries_the_cache_wrapper():
    view = _cached_viewset("CachedViewSet", _FirstSerializer)

    assert view.list is not BaseModelViewSet.list
    assert view.list.__wrapped__ is not None
