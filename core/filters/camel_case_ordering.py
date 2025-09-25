import re
from drf_spectacular.extensions import OpenApiFilterExtension

from djangorestframework_camel_case.util import (
    camelize_re,
    underscore_to_camel as underscore_to_camel_callback,
)
from rest_framework.filters import OrderingFilter
from rest_framework.request import Request
from rest_framework.views import APIView


class CamelCaseOrderingFilter(OrderingFilter):
    @staticmethod
    def camel_to_snake(name: str) -> str:
        name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        name = re.sub("([a-z0-9])([A-Z])", r"\1_\2", name)
        return name.lower()

    def get_ordering(
        self, request: Request, queryset, view: APIView
    ) -> list[str] | None:
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


def snake_to_camel(snake_str):
    return re.sub(camelize_re, underscore_to_camel_callback, snake_str)


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

        if hasattr(view, "ordering_fields") and view.ordering_fields:
            camel_fields = []
            for field in view.ordering_fields:
                camel_field = snake_to_camel(field)
                camel_fields.extend([camel_field, f"-{camel_field}"])

            ordering_param_exists = any(
                param.get("name") == "ordering" for param in parameters
            )

            if ordering_param_exists:
                for param in parameters:
                    if param.get("name") == "ordering":
                        param["description"] = (
                            f"Which field to use when ordering the results. "
                            f"Available fields: {', '.join(camel_fields)}"
                        )
                        if "schema" not in param:
                            param["schema"] = {}
                        param["schema"]["enum"] = camel_fields
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
                            "description": (
                                f"Which field to use when ordering the results. "
                                f"Available fields: {', '.join(camel_fields)}"
                            ),
                            "schema": {"type": "string", "enum": camel_fields},
                        }
                    )

        return parameters
