"""The per-action ordering contract.

A viewset's ``ordering_fields`` describe its own model. An extra
``@action`` that paginates a sub-resource (an author's posts, a user's
favourites) must not inherit them — the old answer was blanking the
attributes inside every such action, which also made ``?ordering=`` a
silent no-op on the storefront's author, category and account pages.
"""

from __future__ import annotations

from types import SimpleNamespace

from rest_framework.decorators import action
from rest_framework.viewsets import ViewSet

from core.filters.camel_case_ordering import (
    ActionOrdering,
    CamelCaseOrderingFilter,
    ordering_for,
)

POSTS = ActionOrdering(
    fields=("created_at", "view_count"), default=("-created_at",)
)


class ParentViewSet(ViewSet):
    ordering_fields = ["id", "email"]
    ordering = ["-created_at"]
    action_ordering = {"declared": POSTS}

    @action(detail=True, methods=["GET"])
    def declared(self, request, pk=None):
        pass

    @action(detail=True, methods=["GET"])
    def undeclared(self, request, pk=None):
        pass


def _view(name: str) -> ParentViewSet:
    view = ParentViewSet()
    view.action = name
    return view


def test_a_standard_action_uses_the_class_level_contract():
    assert ordering_for(_view("list")) == ActionOrdering(
        fields=("id", "email"), default=("-created_at",)
    )


def test_a_declared_extra_action_uses_its_own_contract():
    assert ordering_for(_view("declared")) is POSTS


def test_an_undeclared_extra_action_is_not_sortable_at_all():
    # The parent's columns belong to another model; applying them here
    # is a FieldError at best and a wrong sort at worst. Opt in.
    assert ordering_for(_view("undeclared")) == ActionOrdering(fields=())


def test_the_filter_reads_the_same_contract():
    backend = CamelCaseOrderingFilter()
    view = _view("declared")
    request = SimpleNamespace(
        query_params={"ordering": "viewCount,-createdAt,email"}
    )

    # ``email`` is the parent's column and is dropped; the rest map to
    # snake_case and keep their direction.
    assert backend.get_ordering(request, None, view) == [
        "view_count",
        "-created_at",
    ]
    assert backend.get_default_ordering(view) == ["-created_at"]
    assert backend.get_default_ordering(_view("undeclared")) is None
