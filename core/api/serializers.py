from __future__ import annotations

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
        self.expansion_path = self.context.get("expansion_path", [])

    def to_representation(self, instance) -> Dict[str, any]:
        if self.Meta.model.__name__ in self.expansion_path:
            return super().to_representation(instance)

        data = super().to_representation(instance)
        if self.expand:
            self.expansion_path.append(self.Meta.model.__name__)
            data = self._expand_fields(instance, data)
            self.expansion_path.pop()

        return data

    def _expand_fields(self, instance, data: Dict[str, any]) -> Dict[str, any]:
        for field_name, serializer_class in self.get_expand_fields().items():
            if field_name in self.expansion_path:
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
