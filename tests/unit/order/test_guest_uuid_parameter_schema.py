"""Every order action a guest reaches with ``?uuid=`` publishes that
parameter, so typed clients (the storefront, the agent gateway) can send
it without stepping outside the generated contract."""

from __future__ import annotations

import pytest

from order.views.order import GUEST_UUID_QUERY_ACTIONS, serializers_config


def _operation(schema, operation_id):
    for methods in schema["paths"].values():
        for operation in methods.values():
            if (
                isinstance(operation, dict)
                and operation.get("operationId") == operation_id
            ):
                return operation
    raise AssertionError(f"no operation {operation_id} in the schema")


@pytest.mark.parametrize("action", sorted(GUEST_UUID_QUERY_ACTIONS))
def test_guest_action_declares_the_uuid_query_parameter(openapi_schema, action):
    operation = _operation(
        openapi_schema, serializers_config[action].operation_id
    )
    uuid_params = [
        param
        for param in operation.get("parameters", [])
        if param["name"] == "uuid" and param["in"] == "query"
    ]
    assert len(uuid_params) == 1
    assert uuid_params[0]["schema"]["format"] == "uuid"
    assert not uuid_params[0].get("required", False)
