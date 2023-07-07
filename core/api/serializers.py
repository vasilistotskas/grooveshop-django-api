from __future__ import annotations

from collections import OrderedDict
from typing import Any
from typing import Dict
from typing import Type

from rest_framework import serializers


class BaseExpandSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expand = self.context.get("expand", False)

    def to_representation(self, instance) -> OrderedDict[Any, Any | None]:
        data = super().to_representation(instance)
        if self.expand:
            expand_fields = self.get_expand_fields()
            for field_name, field_serializer_class in expand_fields.items():
                if isinstance(data[field_name], list):
                    data[field_name] = []
                    for item in getattr(instance, field_name).all():
                        data[field_name].append(field_serializer_class(item).data)
                else:
                    data[field_name] = field_serializer_class(
                        getattr(instance, field_name)
                    ).data
        return data

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {}
