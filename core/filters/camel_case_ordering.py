import re
from collections.abc import Sequence
from dataclasses import dataclass

from drf_spectacular.extensions import OpenApiFilterExtension
from rest_framework.filters import OrderingFilter
from rest_framework.request import Request
from rest_framework.views import APIView

from core.utils.string_case import camel_to_snake, snake_to_camel


@dataclass(frozen=True)
class ActionOrdering:
    """The sortable fields and default sort of ONE viewset action.

    A viewset's ``ordering_fields`` describe its own model. An extra
    ``@action`` that lists a sub-resource (an author's posts, a user's
    favourite products) paginates a different model, so the class-level
    list is wrong for it twice over: ``user__first_name`` is not a column
    of ``ProductFavourite``, and the OpenAPI extension below would still
    advertise it. The old answer was ``self.ordering_fields = []`` at the
    top of every such action, which made ``?ordering=`` a silent no-op —
    the storefront's sort control on author pages, category pages and
    every account list rendered a choice that changed nothing.

    Declare the action's own fields in ``action_ordering`` on the viewset
    instead; the filter and the schema both read it.
    """

    fields: tuple[str, ...]
    default: tuple[str, ...] = ()


def _is_extra_action(view: APIView) -> bool:
    """True for a route added with ``@action``, false for list/retrieve/…"""
    action = getattr(view, "action", None)
    if not action:
        return False
    handler = getattr(type(view), action, None)
    return hasattr(handler, "mapping")


def ordering_for(view: APIView) -> ActionOrdering:
    """The ordering contract of the action ``view`` is serving.

    - an entry in ``view.action_ordering`` wins;
    - otherwise a standard action (list, retrieve, …) uses the class-level
      ``ordering_fields`` / ``ordering``;
    - otherwise — an extra ``@action`` with no declaration — nothing is
      sortable and no default sort is applied, because the class-level
      fields belong to another model. Opt in explicitly.
    """
    declared = getattr(view, "action_ordering", {}).get(
        getattr(view, "action", None)
    )
    if declared is not None:
        return declared
    if _is_extra_action(view):
        return ActionOrdering(fields=())
    return ActionOrdering(
        fields=tuple(getattr(view, "ordering_fields", None) or ()),
        default=tuple(getattr(view, "ordering", None) or ()),
    )


def _ordering_csv_pattern(camel_fields: list[str]) -> str:
    """Build a regex matching a comma-separated list of ordering keys.

    ``camel_fields`` already contains both directions (e.g. ``name`` AND
    ``-name``), so the regex is simply ``(<a>|<b>|...)`` repeated with
    comma separators. Fields are ``re.escape``-d so hyphens and other
    metacharacters in the generated list can't break the pattern.
    """
    alternation = "|".join(re.escape(field) for field in camel_fields)
    return rf"^(?:{alternation})(?:,(?:{alternation}))*$"


class CamelCaseOrderingFilter(OrderingFilter):
    @staticmethod
    def camel_to_snake(name: str) -> str:
        """Convert camelCase to snake_case."""
        return camel_to_snake(name)

    def get_valid_fields(self, queryset, view, context=None):
        # Per-action contract, never DRF's "every serializer field" fallback.
        return [(field, field) for field in ordering_for(view).fields]

    def get_default_ordering(self, view):
        return list(ordering_for(view).default) or None

    def get_ordering(
        self, request: Request, queryset, view: APIView
    ) -> Sequence[str] | None:
        ordering_params = self.get_ordering_param(request)
        if not ordering_params:
            return self.get_default_ordering(view)

        fields = []
        for field in ordering_params:
            if field.startswith("-"):
                direction = "-"
                field_name = field[1:]
            else:
                direction = ""
                field_name = field

            snake_case_field = self.camel_to_snake(field_name)
            fields.append(f"{direction}{snake_case_field}")

        valid_fields = self.get_valid_fields(
            queryset, view, {"request": request}
        )
        validated_fields = []

        for field in fields:
            field_without_direction = field.lstrip("-")
            direction = "-" if field.startswith("-") else ""

            if any(
                field_without_direction == valid[0] for valid in valid_fields
            ):
                validated_fields.append(field)

        return validated_fields or self.get_default_ordering(view)

    def get_ordering_param(self, request: Request) -> list[str] | None:
        ordering = request.query_params.get(self.ordering_param)
        if ordering:
            return [param.strip() for param in ordering.split(",")]
        return None


class CamelCaseOrderingFilterExtension(OpenApiFilterExtension):
    target_class = "core.filters.camel_case_ordering.CamelCaseOrderingFilter"
    match_subclasses = True

    def get_schema_operation_parameters(self, auto_schema, *args, **kwargs):
        parameters = super().get_schema_operation_parameters(
            auto_schema, *args, **kwargs
        )

        if parameters is None:
            parameters = []

        view = auto_schema.view
        # The same per-action contract the filter applies, so the schema
        # advertises exactly the keys the action honours — and the
        # storefront's generated Zod (a regex over this list) forwards
        # them instead of stripping them as unknown.
        ordering_fields = ordering_for(view).fields

        if ordering_fields:
            camel_fields = []
            for field in ordering_fields:
                camel_field = snake_to_camel(field)
                camel_fields.extend([camel_field, f"-{camel_field}"])

            ordering_param_exists = any(
                param.get("name") == "ordering" for param in parameters
            )

            description = (
                "Which field(s) to use when ordering the results. "
                "Multiple fields can be combined with commas (e.g. "
                "``-isMain,-createdAt``). Available fields: "
                f"{', '.join(camel_fields)}"
            )
            pattern = _ordering_csv_pattern(camel_fields)

            if ordering_param_exists:
                for param in parameters:
                    if param.get("name") == "ordering":
                        param["description"] = description
                        # Replace the single-value ``enum`` with a
                        # ``type: string`` + regex so the schema matches
                        # the wire format (CSV). openapi-ts turns this
                        # into ``z.string().regex(...)`` which accepts
                        # both single-sort and multi-sort values.
                        param["schema"] = {
                            "type": "string",
                            "pattern": pattern,
                        }
            else:
                filter_instance = None
                if hasattr(view, "filter_backends"):
                    for backend in view.filter_backends:
                        if issubclass(backend, CamelCaseOrderingFilter):
                            filter_instance = backend()
                            break

                if filter_instance:
                    ordering_param_name = getattr(
                        filter_instance, "ordering_param", "ordering"
                    )

                    parameters.append(
                        {
                            "name": ordering_param_name,
                            "in": "query",
                            "required": False,
                            "description": description,
                            "schema": {
                                "type": "string",
                                "pattern": pattern,
                            },
                        }
                    )
        else:
            # An action that honours no ordering must not advertise the
            # parent's list either (the OrderingFilter base class adds
            # ``ordering`` whenever the backend is installed).
            parameters = [
                param for param in parameters if param.get("name") != "ordering"
            ]

        return parameters
