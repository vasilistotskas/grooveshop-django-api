"""Every hand-wired route applies its ``@action(...)`` overrides.

The URL confs call ``ViewSet.as_view({...})`` directly instead of going
through a DRF router, and only the router merges an extra action's
decorator arguments into the view (``SimpleRouter.get_routes``:
``initkwargs.update(action.kwargs)``). Wired by hand, every
``throttle_classes``, ``permission_classes``, ``pagination_class`` or
``queryset`` declared on an action was silently ignored — among them the
payment-attempt throttles of ``create_payment_intent`` and
``retry_payment``. ``RouterActionOverridesMixin`` restores the router's
behaviour; this walks the real URL conf to prove every route gets it.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import decorators
from rest_framework.throttling import AnonRateThrottle
from rest_framework.viewsets import GenericViewSet

from core.api.views import RouterActionOverridesMixin


def _routes(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routes(
                pattern.url_patterns, prefix + str(pattern.pattern)
            )
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _declared_actions(view):
    names = dict.fromkeys((getattr(view, "actions", None) or {}).values())
    return [
        action
        for name in names
        # Extra actions only: the router merges nothing for CRUD.
        if hasattr(action := getattr(view.cls, name, None), "mapping")
    ]


def test_every_routed_action_carries_its_declared_overrides():
    dropped = []
    for route, view in _routes(get_resolver().url_patterns):
        actions = _declared_actions(view)
        for action in actions:
            for key, value in action.kwargs.items():
                # A view mapping several actions cannot carry each one's
                # own name/description/schema; behaviour it must.
                if (
                    len(actions) > 1
                    and key in RouterActionOverridesMixin.PRESENTATION_KWARGS
                ):
                    continue
                if view.initkwargs.get(key, object()) is not value:
                    dropped.append(f"{route} {action.__name__}: {key}")

    assert dropped == []


def test_the_payment_attempt_throttles_reach_the_payment_endpoints():
    view = get_resolver().resolve("/api/v1/order/1/retry-payment").func

    assert {
        throttle.__name__ for throttle in view.initkwargs["throttle_classes"]
    } >= {"PaymentAttemptThrottle", "PaymentAttemptAnonThrottle"}


class _Anon(AnonRateThrottle):
    pass


class _TwoActionsOneRoute(RouterActionOverridesMixin, GenericViewSet):
    @decorators.action(detail=False, methods=["POST"], throttle_classes=[_Anon])
    def apply(self, request):
        raise NotImplementedError

    @decorators.action(
        detail=False, methods=["DELETE"], throttle_classes=[_Anon]
    )
    def remove(self, request):
        raise NotImplementedError

    @decorators.action(detail=False, methods=["PUT"], throttle_classes=[])
    def replace(self, request):
        raise NotImplementedError


def test_actions_sharing_a_route_merge_equal_overrides():
    """Two decorators build two list objects; equal content is agreement."""
    view = _TwoActionsOneRoute.as_view({"post": "apply", "delete": "remove"})

    assert view.initkwargs["throttle_classes"] == [_Anon]


def test_actions_sharing_a_route_must_not_disagree_on_behaviour():
    with pytest.raises(ImproperlyConfigured, match="throttle_classes"):
        _TwoActionsOneRoute.as_view({"post": "apply", "put": "replace"})


def test_presentation_kwargs_are_left_to_each_action():
    view = _TwoActionsOneRoute.as_view({"post": "apply", "delete": "remove"})

    assert "name" not in view.initkwargs


def test_the_mixin_describes_no_operation():
    """drf-spectacular describes an operation with the first docstring in
    the view's MRO, so one on this mixin became the public description of
    every hand-wired viewset without its own (``page-config/admin``)."""
    assert RouterActionOverridesMixin.__doc__ is None
