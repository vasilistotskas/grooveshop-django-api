from typing import Dict
from typing import Type

from rest_framework import serializers


class BaseExpandSerializer(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        expand_context = self.context.get("expand", "false")
        if isinstance(expand_context, str):
            self.expand = expand_context.lower() == "true"
        else:
            self.expand = bool(expand_context)

        expand_fields_context = self.context.get("expand_fields", "")
        self.expand_fields = (
            set(expand_fields_context.split(",")) if expand_fields_context else set()
        )

        self.expansion_path = self.context.get("expansion_path", [])

    def to_representation(self, instance) -> Dict[str, any]:
        ret = super().to_representation(instance)
        request_language = (
            self.context.get("request").query_params.get("language")
            if "request" in self.context
            else None
        )

        if (
            request_language
            and "translations" in ret
            and request_language in ret["translations"]
        ):
            ret["translations"] = {
                request_language: ret["translations"][request_language]
            }

        if self.Meta.model.__name__ in self.expansion_path:
            return ret

        if self.expand:
            self.expansion_path.append(self.Meta.model.__name__)
            ret = self._expand_fields(instance, ret)
            self.expansion_path.pop()

        return ret

    def _expand_fields(self, instance, data: Dict[str, any]) -> Dict[str, any]:
        for field_name, serializer_class in self.get_expand_fields().items():
            if field_name in self.expansion_path or (
                self.expand_fields and field_name not in self.expand_fields
            ):
                continue

            field_value = getattr(instance, field_name, None)
            if field_value is not None:
                is_many = hasattr(field_value, "all")
                data[field_name] = serializer_class(
                    field_value.all() if is_many else field_value,
                    many=is_many,
                    context=self.context,
                ).data
        return data

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {}
