"""A deprecated response field is optional in the schema, and only it."""

from __future__ import annotations

from core.api.schema import (
    OPTIONAL_FIELDS_EXTENSION,
    drop_optional_fields_from_required,
)


def _run(components: dict) -> dict:
    result = {"components": {"schemas": components}}
    return drop_optional_fields_from_required(result, None, None, True)[
        "components"
    ]["schemas"]


def test_takes_only_the_named_fields_out_of_required():
    schemas = _run(
        {
            "A": {
                "required": ["id", "recommendations"],
                OPTIONAL_FIELDS_EXTENSION: ["recommendations"],
            },
            "B": {"required": ["id", "recommendations"]},
        }
    )

    assert schemas["A"]["required"] == ["id"]
    assert OPTIONAL_FIELDS_EXTENSION not in schemas["A"]
    assert schemas["B"]["required"] == ["id", "recommendations"]


def test_drops_an_emptied_required_list():
    schemas = _run(
        {
            "A": {
                "required": ["recommendations"],
                OPTIONAL_FIELDS_EXTENSION: ["recommendations"],
            }
        }
    )

    assert "required" not in schemas["A"]
