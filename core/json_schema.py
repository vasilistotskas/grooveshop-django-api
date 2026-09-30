"""One JSON Schema per structured ``JSONField``.

A field names its schema with ``JSONSchemaValidator("app.json_schemas.x")``
and that one definition validates writes (``full_clean``, the admin
form, DRF serializers, which copy model-field validators), drives the
admin's JSON editor (``admin.base.BaseModelAdmin.formfield_for_dbfield``)
and is what the API publishes. It replaces hand-written Python checkers
that each re-implemented the same shape rules.

The schema is referenced by import path to a function, not embedded:
a migration records the path rather than a copy that drifts, and a
schema built from runtime data (the configured languages, the registered
strategy codes) is built when used, not at import.

Schemas are Draft 2020-12 and self-contained (no ``$ref``): the admin
editor Unfold ships (jedison) is created without a reference resolver.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.utils.module_loading import import_string
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as SchemaError


@deconstructible
class JSONSchemaValidator:
    def __init__(self, schema: str) -> None:
        self.schema_path = schema

    @property
    def schema(self) -> dict[str, Any]:
        return import_string(self.schema_path)()

    def __call__(self, value: Any) -> None:
        validator = Draft202012Validator(
            self.schema, format_checker=FormatChecker()
        )
        errors = sorted(
            validator.iter_errors(value),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
        if errors:
            raise ValidationError(
                [_message(error) for error in errors], code="invalid"
            )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, JSONSchemaValidator)
            and other.schema_path == self.schema_path
        )

    def __hash__(self) -> int:
        return hash(self.schema_path)


def _message(error: SchemaError) -> str:
    """``colors.primaryScale.500: 'red' does not match ...`` — the path
    names the offending entry, so a long value is fixable."""
    path = "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    ).lstrip(".")
    return f"{path}: {error.message}" if path else error.message


def schema_validator(field: Any) -> JSONSchemaValidator | None:
    """The field's ``JSONSchemaValidator``, if it has one."""
    return next(
        (
            validator
            for validator in getattr(field, "validators", ())
            if isinstance(validator, JSONSchemaValidator)
        ),
        None,
    )
