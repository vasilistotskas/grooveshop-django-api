"""An ``ActionConfig(paginated=False)`` action must be unpaginated in the schema.

``paginated=False`` only keeps the page parameters out of the parameters
``create_schema_view_config`` writes. drf-spectacular still wraps a list
response in ``Paginated<Name>List``, and adds the paginator's
parameters, whenever the view has a ``pagination_class``
(``AutoSchema._get_paginator``); only the action's own
``@action(pagination_class=None)`` clears it. ``listLoyaltyTiers`` and
``listRegionsByCountry`` set the flag without it, so ``schema.yml``
promised an envelope for the bare array both return.
"""

from __future__ import annotations

from django.urls import get_resolver

from tests.unit.core.test_routes_apply_action_overrides import _routes

PAGE_PARAMETERS = {"page", "pageSize", "cursor", "limit", "offset"}


def _unpaginated_operation_ids():
    for _route, view in _routes(get_resolver().url_patterns):
        config = getattr(getattr(view, "cls", None), "serializers_config", {})
        for name in (getattr(view, "actions", None) or {}).values():
            action_config = config.get(name)
            if (
                action_config is not None
                and action_config.many
                and not action_config.paginated
            ):
                yield action_config.operation_id


def test_every_unpaginated_action_is_documented_without_pagination(
    openapi_schema,
):
    operations = {
        operation["operationId"]: operation
        for item in openapi_schema["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    operation_ids = set(_unpaginated_operation_ids())
    assert {"listLoyaltyTiers", "listRegionsByCountry"} <= operation_ids

    wrong = []
    for operation_id in sorted(operation_ids):
        operation = operations[operation_id]
        body = operation["responses"]["200"]["content"]["application/json"]
        if "Paginated" in body["schema"].get("$ref", ""):
            wrong.append(f"{operation_id}: {body['schema']['$ref']}")
        names = {p["name"] for p in operation.get("parameters", [])}
        if names & PAGE_PARAMETERS:
            wrong.append(f"{operation_id}: {sorted(names & PAGE_PARAMETERS)}")

    assert wrong == []
