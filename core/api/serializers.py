from __future__ import annotations

from typing import Dict
from typing import Type

from rest_framework import serializers


class BaseExpandSerializer(serializers.ModelSerializer):
    expand = False

    class Meta:
        model = None
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expand = self.context.get("expand", False)

    def to_representation(self, instance):
        if self.context.get("is_expanding", False):
            return super().to_representation(instance)

        data = super().to_representation(instance)
        self.context["is_expanding"] = True

        expand_context = self.context.get("expand", "false")
        if isinstance(expand_context, str):
            expand = expand_context.lower() == "true"
        else:
            expand = bool(expand_context)

        if expand:
            expand_fields = self.get_expand_fields()
            for field_name, field_serializer_class in expand_fields.items():
                field_value = getattr(instance, field_name, None)

                if hasattr(field_value, "all"):
                    data[field_name] = field_serializer_class(
                        field_value.all(), many=True, context=self.context
                    ).data

                elif field_value is not None:
                    data[field_name] = field_serializer_class(
                        field_value, context=self.context
                    ).data

        self.context.pop("is_expanding", None)

        return data

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {}
